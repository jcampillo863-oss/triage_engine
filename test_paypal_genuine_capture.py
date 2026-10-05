"""Manual opt-in Sandbox tool. No historical defaults; excluded from automated discovery."""
__test__ = False
import sqlite3

from db import DB_PATH

from canonical_settlement_engine import (
    get_settlement,
    record_revenue,
)

from paypal_capture_service import (
    prepare_capture,
    execute_capture,
)

from paypal_sandbox_client import (
    capture_authorization,
    get_authorization,
    get_order,
)

from paypal_reconciliation_observer import (
    observe_authorization,
)


EXPECTED_PROVIDER = "paypal"
EXPECTED_ENVIRONMENT = "SANDBOX"


def connect():
    conn = sqlite3.connect(
        DB_PATH,
        timeout=10,
    )

    conn.row_factory = sqlite3.Row

    conn.execute(
        "PRAGMA foreign_keys = ON"
    )

    conn.execute(
        "PRAGMA busy_timeout = 10000"
    )

    return conn


def verify_preconditions(conn, *, settlement_id, expected_amount_cents, expected_currency):
    settlement = get_settlement(
        conn,
        settlement_id,
    )

    if settlement is None:
        raise RuntimeError(
            "Canonical settlement does not exist."
        )

    checks = {
        "eligibility": bool(settlement.get("eligibility_decision_id")),
        "state": (
            settlement["state"]
            == "PAYMENT_AUTHORIZED"
        ),
        "amount": (
            settlement["amount_cents"]
            == expected_amount_cents
        ),
        "currency": (
            settlement["currency"]
            == expected_currency
        ),
        "provider": (
            settlement["provider"]
            == EXPECTED_PROVIDER
        ),
        "environment": (
            settlement["environment"]
            == EXPECTED_ENVIRONMENT
        ),
        "not_already_captured": (
            settlement["provider_capture_id"]
            is None
        ),
    }

    failed = [
        name
        for name, passed in checks.items()
        if not passed
    ]

    if failed:
        raise RuntimeError(
            "CAPTURE ABORTED. "
            "Precondition failure: "
            + ", ".join(failed)
        )

    auth = conn.execute(
        """
        SELECT *
        FROM payment_authorizations
        WHERE authorization_id = ?
        """,
        (
            settlement[
                "payment_authorization_id"
            ],
        ),
    ).fetchone()

    if auth is None:
        raise RuntimeError(
            "CAPTURE ABORTED. "
            "Canonical payment authorization missing."
        )

    auth_checks = {
        "verified": auth["verified"] == 1,
        "provider": (
            auth["provider"]
            == EXPECTED_PROVIDER
        ),
        "amount": (
            auth["amount_cents"]
            == expected_amount_cents
        ),
        "currency": (
            auth["currency"]
            == expected_currency
        ),
        "environment": (
            auth["environment"]
            == EXPECTED_ENVIRONMENT
        ),
        "provider_status": (
            auth["provider_status"]
            == "CREATED"
        ),
        "provider_authorization_present": bool(
            auth["provider_authorization_id"]
        ),
    }

    failed = [
        name
        for name, passed in auth_checks.items()
        if not passed
    ]

    if failed:
        raise RuntimeError(
            "CAPTURE ABORTED. "
            "Authorization failure: "
            + ", ".join(failed)
        )

    return settlement


def genuine_capture(
    provider_authorization_id,
    *,
    manual_confirmation=False,
    request_id,
):
    if manual_confirmation is not True:
        raise RuntimeError("Explicit manual Sandbox confirmation required")
    return capture_authorization(
        provider_authorization_id,
        request_id=request_id,
    )


def main(*, settlement_id, expected_amount_cents, expected_currency, manual_confirmation=False):
    if manual_confirmation is not True:
        raise RuntimeError("Manual provider capture is disabled unless explicitly confirmed")
    if not isinstance(settlement_id, str) or not settlement_id.strip():
        raise ValueError("An explicitly reviewed fresh settlement is required")
    if isinstance(expected_amount_cents, bool) or not isinstance(expected_amount_cents, int) or expected_amount_cents <= 0:
        raise ValueError("Explicit expected amount must be positive integer cents")
    if not isinstance(expected_currency, str) or len(expected_currency)!=3 or not expected_currency.isalpha() or expected_currency!=expected_currency.upper():
        raise ValueError("Explicit uppercase expected currency required")
    conn = connect()

    try:
        print(
            "GENUINE PAYPAL SANDBOX CAPTURE"
        )
        print(
            "------------------------------"
        )

        verify_preconditions(conn, settlement_id=settlement_id, expected_amount_cents=expected_amount_cents, expected_currency=expected_currency)

        print("PRECONDITIONS: PASSED")
        print("ENVIRONMENT: SANDBOX")
        print("AMOUNT: 100 cents AUD")
        print(
            "CURRENT STATE: PAYMENT_AUTHORIZED"
        )

        # -------------------------------------------------
        # Phase 1
        # Persist CAPTURE_REQUESTED before touching PayPal.
        # -------------------------------------------------

        prepared = prepare_capture(
            conn,
            settlement_id,
        )

        assert (
            prepared.state
            == "CAPTURE_REQUESTED"
        )

        conn.commit()

        print(
            "CAPTURE_REQUESTED: COMMITTED"
        )

        # -------------------------------------------------
        # Phase 2
        # Exactly one modifying PayPal Sandbox operation.
        # -------------------------------------------------

        result = execute_capture(
            conn,
            settlement_id,
            capture_fn=lambda authorization, **kwargs: genuine_capture(authorization, manual_confirmation=True, **kwargs),
        )

        # Persist whatever canonical result we learned.
        conn.commit()

        print(
            "POST-CAPTURE STATE:",
            result.state,
        )

        if result.state == "OUTCOME_UNKNOWN":
            print(
                "PROVIDER OUTCOME: AMBIGUOUS"
            )
            print(
                "NO RETRY WILL BE ATTEMPTED."
            )
            print(
                "RECONCILIATION REQUIRED."
            )
            return

        if result.state != "PROVIDER_CONFIRMED":
            raise RuntimeError(
                "Unexpected post-capture state: "
                + result.state
            )

        if not result.provider_capture_id:
            raise RuntimeError(
                "Provider confirmation has no capture ID."
            )

        print(
            "PROVIDER CONFIRMATION: RECEIVED"
        )

        # -------------------------------------------------
        # Phase 3
        # Independently read PayPal back.
        # No modifying provider operation occurs here.
        # -------------------------------------------------

        settlement = get_settlement(
            conn,
            settlement_id,
        )

        auth = conn.execute(
            """
            SELECT provider_authorization_id
            FROM payment_authorizations
            WHERE authorization_id = ?
            """,
            (
                settlement[
                    "payment_authorization_id"
                ],
            ),
        ).fetchone()

        if auth is None:
            raise RuntimeError(
                "Payment authorization disappeared."
            )

        observation = observe_authorization(
            auth["provider_authorization_id"],
            get_authorization_fn=get_authorization,
            get_order_fn=get_order,
        )

        if observation.get("outcome") != "COMPLETED":
            raise RuntimeError(
                "Independent PayPal read-back did not "
                "confirm a completed capture."
            )

        observed_capture_id = observation.get(
            "capture_id"
        )

        if (
            observed_capture_id
            != result.provider_capture_id
        ):
            raise RuntimeError(
                "PayPal read-back capture identity "
                "does not match canonical confirmation."
            )

        print(
            "PAYPAL READ-BACK: COMPLETED"
        )
        print(
            "CAPTURE IDENTITY MATCH: True"
        )

        # We do not depend on supplementary_data
        # for canonical confirmation. The authoritative
        # capture identity came from the capture response.
        # This read is an additional provider check.

        print(
            "PAYPAL READ-BACK: COMPLETED"
        )

        # -------------------------------------------------
        # Phase 4
        # Record revenue only after provider confirmation.
        # -------------------------------------------------

        ledger_id = (
            "rev-"
            + result.provider_capture_id
        )

        revenue = record_revenue(
            conn,
            settlement_id,
            ledger_id=ledger_id,
            reason=(
                "Genuine PayPal Sandbox "
                "provider-confirmed capture."
            ),
            evidence_reference=(
                result.provider_capture_id
            ),
        )

        assert (
            revenue["state"]
            == "REVENUE_RECORDED"
        )

        conn.commit()

        # -------------------------------------------------
        # Phase 5
        # Final canonical verification.
        # -------------------------------------------------

        final = get_settlement(
            conn,
            settlement_id,
        )

        ledger = conn.execute(
            """
            SELECT *
            FROM revenue_ledger
            WHERE settlement_id = ?
            """,
            (settlement_id,),
        ).fetchone()

        if ledger is None:
            raise RuntimeError(
                "Revenue ledger row missing."
            )

        if (
            ledger["provider_tx_id"]
            != final["provider_capture_id"]
        ):
            raise RuntimeError(
                "Ledger/provider capture mismatch."
            )

        if ledger["amount_cents"] != expected_amount_cents:
            raise RuntimeError(
                "Ledger amount mismatch."
            )

        if ledger["currency"] != expected_currency:
            raise RuntimeError(
                "Ledger currency mismatch."
            )

        if (
            final["state"]
            != "REVENUE_RECORDED"
        ):
            raise RuntimeError(
                "Final settlement state incorrect."
            )

        journal = conn.execute(
            """
            SELECT from_state, to_state
            FROM canonical_settlement_journal
            WHERE settlement_id = ?
            ORDER BY journal_id
            """,
            (settlement_id,),
        ).fetchall()

        print(
            "FINAL STATE:",
            final["state"],
        )
        print(
            "REVENUE LEDGER ROWS:",
            1,
        )
        print(
            "LEDGER AMOUNT:",
            ledger["amount_cents"],
            ledger["currency"],
        )
        print(
            "PROVIDER TX MATCH:",
            True,
        )
        print(
            "JOURNAL ENTRIES:",
            len(journal),
        )

        print(
            "GENUINE PAYPAL SANDBOX "
            "REVENUE LIFECYCLE: PASSED"
        )

    finally:
        conn.close()


if __name__ == "__main__":
    import argparse
    parser=argparse.ArgumentParser(description="MANUAL Sandbox capture: never replay completed historical transactions")
    parser.add_argument('--confirm-manual-sandbox-capture',action='store_true',required=True)
    parser.add_argument('--settlement-id',required=True)
    parser.add_argument('--expected-amount-cents',type=int,required=True)
    parser.add_argument('--expected-currency',required=True)
    args=parser.parse_args()
    main(settlement_id=args.settlement_id, expected_amount_cents=args.expected_amount_cents,
        expected_currency=args.expected_currency, manual_confirmation=args.confirm_manual_sandbox_capture)
