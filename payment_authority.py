from contextlib import nullcontext
import hashlib
import json
from datetime import datetime, timezone

import db


VALID_ENVIRONMENTS = {"TEST", "SANDBOX", "PRODUCTION"}
VERIFICATION_POLICY = "contract_payment_authorization"
POLICY_VERSION = "1"


class PaymentAuthorityError(ValueError):
    pass


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def stable_id(prefix, *parts):
    raw = ":".join(str(part) for part in parts)

    digest = hashlib.sha256(
        raw.encode("utf-8")
    ).hexdigest()[:24]

    return f"{prefix}_{digest}"


def normalize_currency(currency):
    value = (currency or "").strip().upper()

    if len(value) != 3 or not value.isalpha():
        raise PaymentAuthorityError(
            "Currency must be a three-letter code."
        )

    return value


def validate_provider_event(event, environment):
    if not isinstance(event, dict):
        raise PaymentAuthorityError(
            "Provider event must be a dictionary."
        )

    environment = (environment or "").strip().upper()

    if environment not in VALID_ENVIRONMENTS:
        raise PaymentAuthorityError(
            "Invalid environment."
        )

    if event.get("authenticated") is not True:
        raise PaymentAuthorityError(
            "Provider event is not authenticated."
        )

    provider = (event.get("provider") or "").strip().lower()
    provider_event_id = (
        event.get("provider_event_id") or ""
    ).strip()
    provider_authorization_id = (
        event.get("provider_authorization_id") or ""
    ).strip()
    event_type = (event.get("event_type") or "").strip()
    provider_status = (
        event.get("provider_status") or ""
    ).strip().upper()

    amount_cents = event.get("amount_cents")

    if not provider:
        raise PaymentAuthorityError(
            "Provider is required."
        )

    if not provider_event_id:
        raise PaymentAuthorityError(
            "Provider event ID is required."
        )

    if not provider_authorization_id:
        raise PaymentAuthorityError(
            "Provider authorization ID is required."
        )

    if not event_type:
        raise PaymentAuthorityError(
            "Provider event type is required."
        )

    if not provider_status:
        raise PaymentAuthorityError(
            "Provider status is required."
        )

    if (
        not isinstance(amount_cents, int)
        or isinstance(amount_cents, bool)
        or amount_cents <= 0
    ):
        raise PaymentAuthorityError(
            "Provider amount must be positive integer cents."
        )

    currency = normalize_currency(
        event.get("currency")
    )

    return {
        "provider": provider,
        "provider_event_id": provider_event_id,
        "provider_authorization_id":
            provider_authorization_id,
        "event_type": event_type,
        "provider_status": provider_status,
        "amount_cents": amount_cents,
        "currency": currency,
        "authenticated": True,
        "raw_payload": event.get("raw_payload"),
        "environment": environment,
    }


def _establish_payment_authorization(
    *,
    provider_event,
    task_id,
    contract_acceptance_decision_id,
    expected_amount_cents,
    expected_currency,
    expected_provider,
    environment,
    _conn=None,
    _provider_receipt=None,
    _production_obligation=None,
):
    task_id = (task_id or "").strip()

    if not task_id:
        raise PaymentAuthorityError(
            "task_id is required."
        )

    environment = (environment or "").strip().upper()

    if environment not in VALID_ENVIRONMENTS:
        raise PaymentAuthorityError(
            "Invalid environment."
        )

    if (
        not isinstance(expected_amount_cents, int)
        or isinstance(expected_amount_cents, bool)
        or expected_amount_cents <= 0
    ):
        raise PaymentAuthorityError(
            "Expected amount must be positive integer cents."
        )

    expected_currency = normalize_currency(
        expected_currency
    )

    expected_provider = (
        expected_provider or ""
    ).strip().lower()

    if not expected_provider:
        raise PaymentAuthorityError(
            "Expected provider is required."
        )

    normalized = validate_provider_event(
        provider_event,
        environment,
    )

    if environment == "PRODUCTION":
        from paypal_client import evidence_data,ProviderBoundaryError
        from production_payment_flow import obligation
        if _conn is None:
            raise PaymentAuthorityError("Production requires the trusted atomic payment path")
        try:
            proof=evidence_data(_provider_receipt)
            obligation_record=obligation(_conn,_production_obligation)
            required=dict(task_id=task_id,contract_acceptance_decision_id=contract_acceptance_decision_id,
                amount_cents=expected_amount_cents,currency=expected_currency,provider=expected_provider,
                environment=environment)
            if any(obligation_record[k]!=value for k,value in required.items()):
                raise ProviderBoundaryError("Obligation mismatch")
            if (proof['environment']!='PRODUCTION' or proof['endpoint']!='https://api-m.paypal.com'
                or proof['obligation_id']!=_production_obligation
                or proof['payee_id']!=obligation_record['payee_id']
                or proof['amount_cents']!=expected_amount_cents or proof['currency']!=expected_currency
                or normalized['provider_authorization_id']!=proof['authorization_id']
                or normalized['amount_cents']!=proof['amount_cents']
                or normalized['currency']!=proof['currency'] or normalized['provider']!='paypal'):
                raise ProviderBoundaryError("Provider proof mismatch")
            binding=_conn.execute("SELECT order_id FROM payment_order_bindings WHERE obligation_id=?",
                                  (_production_obligation,)).fetchone()
            if binding is None or binding[0]!=proof['order_id']:
                raise ProviderBoundaryError("Order binding mismatch")
        except Exception:
            raise PaymentAuthorityError("Trusted Production provider evidence and exact obligation required") from None

    now = utc_now()

    with (nullcontext(_conn) if _conn is not None else db.get_db()) as conn:
        decision = conn.execute(
            """
            SELECT *
            FROM contract_acceptance_decisions
            WHERE decision_id = ?
            """,
            (contract_acceptance_decision_id,),
        ).fetchone()

        if not decision:
            raise PaymentAuthorityError(
                "Contract acceptance decision does not exist."
            )

        if decision["accepted"] != 1:
            raise PaymentAuthorityError(
                "Contract was not accepted."
            )

        if decision["task_id"] != task_id:
            raise PaymentAuthorityError(
                "Contract acceptance task mismatch."
            )

        if decision["environment"] != environment:
            raise PaymentAuthorityError(
                "Contract acceptance environment mismatch."
            )

        event_id = stable_id(
            "pae",
            normalized["provider"],
            normalized["provider_event_id"],
            environment,
        )

        existing_event = conn.execute(
            """
            SELECT *
            FROM payment_authorization_events
            WHERE provider = ?
              AND provider_event_id = ?
              AND environment = ?
            """,
            (
                normalized["provider"],
                normalized["provider_event_id"],
                environment,
            ),
        ).fetchone()

        if existing_event:
            immutable = {
                "event_id": event_id,
                "provider_authorization_id":
                    normalized["provider_authorization_id"],
                "event_type":
                    normalized["event_type"],
                "provider_status":
                    normalized["provider_status"],
                "amount_cents":
                    normalized["amount_cents"],
                "currency":
                    normalized["currency"],
                "authenticated": 1,
            }

            for field, expected in immutable.items():
                if existing_event[field] != expected:
                    raise PaymentAuthorityError(
                        "Provider event replay conflicts "
                        f"on {field}."
                    )
        else:
            raw_payload = normalized["raw_payload"]

            if raw_payload is not None:
                if isinstance(raw_payload, str):
                    stored_payload = raw_payload
                else:
                    stored_payload = json.dumps(
                        raw_payload,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
            else:
                stored_payload = json.dumps(
                    provider_event,
                    sort_keys=True,
                    separators=(",", ":"),
                )

            conn.execute(
                """
                INSERT INTO payment_authorization_events (
                    event_id,
                    provider,
                    provider_event_id,
                    provider_authorization_id,
                    event_type,
                    provider_status,
                    amount_cents,
                    currency,
                    authenticated,
                    raw_payload,
                    environment,
                    observed_at,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    normalized["provider"],
                    normalized["provider_event_id"],
                    normalized["provider_authorization_id"],
                    normalized["event_type"],
                    normalized["provider_status"],
                    normalized["amount_cents"],
                    normalized["currency"],
                    1,
                    stored_payload,
                    environment,
                    now,
                    now,
                ),
            )

        reasons = []

        if normalized["provider"] != expected_provider:
            reasons.append("Provider mismatch.")

        if normalized["amount_cents"] != expected_amount_cents:
            reasons.append("Amount mismatch.")

        if normalized["currency"] != expected_currency:
            reasons.append("Currency mismatch.")

        #
        # Provider-specific status interpretation does NOT
        # belong here. The provider adapter must only send
        # an authorization event after it has established
        # that the provider state represents authorization.
        #

        verified = len(reasons) == 0

        verification_id = stable_id(
            "pav",
            event_id,
            task_id,
            contract_acceptance_decision_id,
            VERIFICATION_POLICY,
            POLICY_VERSION,
        )

        existing_verification = conn.execute(
            """
            SELECT *
            FROM payment_authorization_verifications
            WHERE verification_id = ?
            """,
            (verification_id,),
        ).fetchone()

        rejection_json = json.dumps(reasons)

        if existing_verification:
            if (
                bool(existing_verification["verified"])
                != verified
                or
                existing_verification["task_id"]
                != task_id
                or
                existing_verification[
                    "contract_acceptance_decision_id"
                ]
                != contract_acceptance_decision_id
            ):
                raise PaymentAuthorityError(
                    "Verification replay conflicts "
                    "with existing canonical result."
                )
        else:
            conn.execute(
                """
                INSERT INTO payment_authorization_verifications (
                    verification_id,
                    authorization_event_id,
                    task_id,
                    contract_acceptance_decision_id,
                    verification_policy,
                    policy_version,
                    verified,
                    rejection_reasons,
                    environment,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    verification_id,
                    event_id,
                    task_id,
                    contract_acceptance_decision_id,
                    VERIFICATION_POLICY,
                    POLICY_VERSION,
                    1 if verified else 0,
                    rejection_json,
                    environment,
                    now,
                ),
            )

        authorization = None

        if verified:
            authorization_id = stable_id(
                "pa",
                normalized["provider"],
                normalized["provider_authorization_id"],
                environment,
            )

            existing_auth = conn.execute(
                """
                SELECT *
                FROM payment_authorizations
                WHERE provider = ?
                  AND provider_authorization_id = ?
                """,
                (
                    normalized["provider"],
                    normalized["provider_authorization_id"],
                ),
            ).fetchone()

            if existing_auth:
                required = {
                    "authorization_id": authorization_id,
                    "task_id": task_id,
                    "contract_acceptance_decision_id":
                        contract_acceptance_decision_id,
                    "amount_cents": expected_amount_cents,
                    "currency": expected_currency,
                    "environment": environment,
                    "verified": 1,
                }

                for field, expected in required.items():
                    if existing_auth[field] != expected:
                        raise PaymentAuthorityError(
                            "Provider authorization is already "
                            "bound to conflicting marketplace data."
                        )

                authorization = existing_auth

            else:
                conn.execute(
                    """
                    INSERT INTO payment_authorizations (
                        authorization_id,
                        task_id,
                        contract_acceptance_decision_id,
                        provider,
                        provider_authorization_id,
                        amount_cents,
                        currency,
                        provider_status,
                        verified,
                        environment,
                        verified_at,
                        created_at,
                        updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        authorization_id,
                        task_id,
                        contract_acceptance_decision_id,
                        normalized["provider"],
                        normalized["provider_authorization_id"],
                        expected_amount_cents,
                        expected_currency,
                        normalized["provider_status"],
                        1,
                        environment,
                        now,
                        now,
                        now,
                    ),
                )

                authorization = conn.execute(
                    """
                    SELECT *
                    FROM payment_authorizations
                    WHERE authorization_id = ?
                    """,
                    (authorization_id,),
                ).fetchone()

        if _conn is None:
            conn.commit()

        event_row = conn.execute(
            """
            SELECT *
            FROM payment_authorization_events
            WHERE event_id = ?
            """,
            (event_id,),
        ).fetchone()

        verification_row = conn.execute(
            """
            SELECT *
            FROM payment_authorization_verifications
            WHERE verification_id = ?
            """,
            (verification_id,),
        ).fetchone()

    return {
        "event": event_row,
        "verification": verification_row,
        "authorization": authorization,
    }


def establish_payment_authorization(**fields):
    """Compatibility entry; Production requires a trusted provider receipt."""
    if str(fields.get('environment','')).strip().upper()=='PRODUCTION':
        raise PaymentAuthorityError("Production requires immutable obligation and trusted provider evidence")
    if '_conn' in fields:
        raise PaymentAuthorityError("Caller connection override forbidden")
    return _establish_payment_authorization(**fields)
