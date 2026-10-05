import hashlib
from datetime import datetime, timezone

import db


ALLOWED_TRANSITIONS = {
    "PAYMENT_AUTHORIZED": {
        "CAPTURE_REQUESTED",
        "FAILED",
        "MANUAL_REVIEW_REQUIRED",
    },
    "CAPTURE_REQUESTED": {
        "OUTCOME_UNKNOWN",
        "PROVIDER_CONFIRMED",
        "FAILED",
    },
    "OUTCOME_UNKNOWN": {
        "RECONCILING",
        "MANUAL_REVIEW_REQUIRED",
    },
    "RECONCILING": {
        "PROVIDER_CONFIRMED",
        "FAILED",
        "MANUAL_REVIEW_REQUIRED",
    },
    "PROVIDER_CONFIRMED": {
        "MANUAL_REVIEW_REQUIRED",
    },
    "REVENUE_RECORDED": set(),
    "FAILED": set(),
    "MANUAL_REVIEW_REQUIRED": set(),
}


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def generate_settlement_id(obligation_id):
    normalized = obligation_id.strip().lower()
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:20]
    return f"cstl_{digest}"


def get_settlement(conn, settlement_id):
    row = conn.execute(
        """
        SELECT *
        FROM canonical_settlements
        WHERE settlement_id = ?
        """,
        (settlement_id,),
    ).fetchone()

    return dict(row) if row else None


def create_settlement(
    conn,
    *,
    task_id,
    obligation_id,
    contract_acceptance_decision_id,
    payment_authorization_id,
    amount_cents,
    currency,
    provider,
    environment,
    eligibility_decision_id=None,
):
    settlement_id = generate_settlement_id(obligation_id)

    existing = get_settlement(conn, settlement_id)

    if not isinstance(eligibility_decision_id, str) or not eligibility_decision_id.strip():
        raise ValueError("Approved matching settlement eligibility is required")
    eligibility = conn.execute(
        "SELECT * FROM settlement_eligibility_decisions WHERE decision_id=?",
        (eligibility_decision_id,),
    ).fetchone()
    if eligibility is None or eligibility["approved"] != 1:
        raise ValueError("Settlement eligibility is not approved")
    if (eligibility["task_id"] != task_id or eligibility["environment"] != environment
        or eligibility["contract_acceptance_decision_id"] != contract_acceptance_decision_id
        or eligibility["policy_name"] != "canonical_settlement_eligibility" or eligibility["policy_version"] != "1"):
        raise ValueError("Settlement eligibility does not match the requested obligation")
    if existing:
        expected = dict(task_id=task_id, obligation_id=obligation_id,
            contract_acceptance_decision_id=contract_acceptance_decision_id,
            payment_authorization_id=payment_authorization_id, amount_cents=amount_cents,
            currency=currency, provider=provider, environment=environment,
            eligibility_decision_id=eligibility_decision_id)
        if any(existing[key] != value for key, value in expected.items()):
            raise ValueError("Settlement replay conflicts with immutable obligation")
        return existing

    acceptance = conn.execute(
        """
        SELECT *
        FROM contract_acceptance_decisions
        WHERE decision_id = ?
        """,
        (contract_acceptance_decision_id,),
    ).fetchone()

    if acceptance is None:
        raise ValueError("Contract acceptance decision does not exist.")

    if acceptance["accepted"] != 1:
        raise ValueError("Contract acceptance decision is not accepted.")

    if acceptance["task_id"] != task_id:
        raise ValueError("Acceptance decision task_id does not match.")

    if acceptance["environment"] != environment:
        raise ValueError("Acceptance decision environment does not match.")

    authorization = conn.execute(
        """
        SELECT *
        FROM payment_authorizations
        WHERE authorization_id = ?
        """,
        (payment_authorization_id,),
    ).fetchone()

    if authorization is None:
        raise ValueError("Payment authorization does not exist.")

    if authorization["verified"] != 1:
        raise ValueError("Payment authorization is not verified.")

    if authorization["task_id"] != task_id:
        raise ValueError("Payment authorization task_id does not match.")

    if authorization["environment"] != environment:
        raise ValueError("Payment authorization environment does not match.")

    if authorization["amount_cents"] != amount_cents:
        raise ValueError("Payment authorization amount does not match.")

    if authorization["currency"] != currency:
        raise ValueError("Payment authorization currency does not match.")

    if authorization["provider"] != provider:
        raise ValueError("Payment authorization provider does not match.")

    if (
        authorization["contract_acceptance_decision_id"]
        != contract_acceptance_decision_id
    ):
        raise ValueError(
            "Payment authorization is not linked to this "
            "contract acceptance decision."
        )

    now = utc_now()

    conn.execute(
        """
        INSERT INTO canonical_settlements (
            settlement_id,
            task_id,
            obligation_id,
            contract_acceptance_decision_id,
            payment_authorization_id,
            eligibility_decision_id,
            amount_cents,
            currency,
            state,
            provider,
            provider_capture_id,
            environment,
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?)
        """,
        (
            settlement_id,
            task_id,
            obligation_id,
            contract_acceptance_decision_id,
            payment_authorization_id,
            eligibility_decision_id,
            amount_cents,
            currency,
            "PAYMENT_AUTHORIZED",
            provider,
            environment,
            now,
            now,
        ),
    )

    conn.execute(
        """
        INSERT INTO canonical_settlement_journal (
            settlement_id,
            from_state,
            to_state,
            reason,
            evidence_reference,
            environment,
            created_at
        )
        VALUES (?, NULL, ?, ?, ?, ?, ?)
        """,
        (
            settlement_id,
            "PAYMENT_AUTHORIZED",
            "Canonical settlement created from verified prerequisites.",
            payment_authorization_id,
            environment,
            now,
        ),
    )

    return get_settlement(conn, settlement_id)


def transition_settlement(
    conn,
    settlement_id,
    new_state,
    *,
    reason,
    evidence_reference=None,
    provider_capture_id=None,
):
    current = get_settlement(conn, settlement_id)

    if current is None:
        raise ValueError("Settlement does not exist.")

    old_state = current["state"]

    if new_state == old_state:
        return current

    allowed = ALLOWED_TRANSITIONS.get(old_state)

    if allowed is None or new_state not in allowed:
        raise ValueError(
            f"Illegal settlement transition: "
            f"{old_state} -> {new_state}"
        )

    if new_state == "PROVIDER_CONFIRMED" and not provider_capture_id:
        raise ValueError(
            "PROVIDER_CONFIRMED requires provider_capture_id."
        )

    now = utc_now()

    if provider_capture_id is not None:
        conn.execute(
            """
            UPDATE canonical_settlements
            SET state = ?,
                provider_capture_id = ?,
                updated_at = ?
            WHERE settlement_id = ?
            """,
            (
                new_state,
                provider_capture_id,
                now,
                settlement_id,
            ),
        )
    else:
        conn.execute(
            """
            UPDATE canonical_settlements
            SET state = ?,
                updated_at = ?
            WHERE settlement_id = ?
            """,
            (
                new_state,
                now,
                settlement_id,
            ),
        )

    conn.execute(
        """
        INSERT INTO canonical_settlement_journal (
            settlement_id,
            from_state,
            to_state,
            reason,
            evidence_reference,
            environment,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            settlement_id,
            old_state,
            new_state,
            reason,
            evidence_reference,
            current["environment"],
            now,
        ),
    )

    return get_settlement(conn, settlement_id)


def record_revenue(
    conn,
    settlement_id,
    *,
    ledger_id,
    reason,
    evidence_reference=None,
):
    settlement = get_settlement(conn, settlement_id)

    if settlement is None:
        raise ValueError("Settlement does not exist.")

    existing = conn.execute(
        """
        SELECT *
        FROM revenue_ledger
        WHERE settlement_id = ?
        """,
        (settlement_id,),
    ).fetchone()

    # Idempotent replay:
    # if revenue was already recorded, verify that the persisted
    # ledger agrees with the settlement before returning success.
    if settlement["state"] == "REVENUE_RECORDED":
        if existing is None:
            raise ValueError(
                "Settlement is REVENUE_RECORDED but ledger entry is missing."
            )

        if existing["provider"] != settlement["provider"]:
            raise ValueError(
                "Existing ledger provider does not match settlement."
            )

        if existing["provider_tx_id"] != settlement["provider_capture_id"]:
            raise ValueError(
                "Existing ledger provider transaction does not match settlement."
            )

        if existing["amount_cents"] != settlement["amount_cents"]:
            raise ValueError(
                "Existing ledger amount does not match settlement."
            )

        if existing["currency"] != settlement["currency"]:
            raise ValueError(
                "Existing ledger currency does not match settlement."
            )

        if existing["environment"] != settlement["environment"]:
            raise ValueError(
                "Existing ledger environment does not match settlement."
            )

        return settlement

    if settlement["state"] != "PROVIDER_CONFIRMED":
        raise ValueError(
            "Revenue can only be recorded from PROVIDER_CONFIRMED."
        )

    provider_tx_id = settlement["provider_capture_id"]

    if not provider_tx_id:
        raise ValueError(
            "Revenue cannot be recorded without provider confirmation ID."
        )

    # A ledger row existing before the settlement reaches
    # REVENUE_RECORDED indicates inconsistent state.
    if existing is not None:
        raise ValueError(
            "Revenue ledger entry exists before settlement "
            "reached REVENUE_RECORDED."
        )

    now = utc_now()

    conn.execute(
        """
        INSERT INTO revenue_ledger (
            ledger_id,
            settlement_id,
            task_id,
            provider,
            provider_tx_id,
            amount_cents,
            currency,
            environment,
            recorded_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            ledger_id,
            settlement_id,
            settlement["task_id"],
            settlement["provider"],
            provider_tx_id,
            settlement["amount_cents"],
            settlement["currency"],
            settlement["environment"],
            now,
        ),
    )

    # REVENUE_RECORDED is deliberately not exposed through the
    # ordinary public settlement state machine. Reaching this state
    # requires a successful revenue-ledger write in this operation.
    final_time = utc_now()

    cursor = conn.execute(
        """
        UPDATE canonical_settlements
        SET state = ?,
            updated_at = ?
        WHERE settlement_id = ?
          AND state = ?
        """,
        (
            "REVENUE_RECORDED",
            final_time,
            settlement_id,
            "PROVIDER_CONFIRMED",
        ),
    )

    if cursor.rowcount != 1:
        raise ValueError(
            "Settlement state changed before revenue finalization."
        )

    conn.execute(
        """
        INSERT INTO canonical_settlement_journal (
            settlement_id,
            from_state,
            to_state,
            reason,
            evidence_reference,
            environment,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            settlement_id,
            "PROVIDER_CONFIRMED",
            "REVENUE_RECORDED",
            reason,
            evidence_reference or ledger_id,
            settlement["environment"],
            final_time,
        ),
    )

    return get_settlement(conn, settlement_id)