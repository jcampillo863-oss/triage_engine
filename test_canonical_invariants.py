import uuid
from datetime import datetime, timezone

import db
import canonical_settlement_engine as engine
from eligibility_test_support import seed_test_eligibility


def now():
    return datetime.now(timezone.utc).isoformat()


passed = 0
failed = 0


def expect_rejection(name, operation):
    global passed, failed

    try:
        operation()
        print(f"[FAIL] {name}: operation was accepted")
        failed += 1
    except (ValueError, Exception) as exc:
        print(f"[PASS] {name}")
        print(f"       {type(exc).__name__}: {exc}")
        passed += 1


suffix = uuid.uuid4().hex[:8]

with db.get_db() as conn:
    try:
        conn.execute("BEGIN")
        timestamp = now()

        task_id = f"negative_task_{suffix}"
        event_id = f"negative_evt_{suffix}"
        acceptance_id = f"negative_accept_{suffix}"
        auth_id = f"negative_auth_{suffix}"

        # ------------------------------------------------------------
        # Valid acceptance event
        # ------------------------------------------------------------

        conn.execute(
            """
            INSERT INTO external_acceptance_events (
                event_id, task_id, source, external_event_id,
                event_type, authenticated, environment,
                observed_at, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                task_id,
                "TEST_HARNESS",
                f"external_{suffix}",
                "TEST_ACCEPTANCE",
                1,
                "TEST",
                timestamp,
                timestamp,
            ),
        )

        conn.execute(
            """
            INSERT INTO contract_acceptance_decisions (
                decision_id, task_id, acceptance_event_id,
                policy_name, policy_version, accepted,
                environment, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                acceptance_id,
                task_id,
                event_id,
                "test_contract_policy",
                "1",
                1,
                "TEST",
                timestamp,
            ),
        )

        eligibility_id = seed_test_eligibility(conn, task_id=task_id,
            acceptance_id=acceptance_id, event_id=event_id)

        # ------------------------------------------------------------
        # Unverified authorization
        # ------------------------------------------------------------

        conn.execute(
            """
            INSERT INTO payment_authorizations (
                authorization_id, task_id,
                contract_acceptance_decision_id,
                provider, provider_authorization_id,
                amount_cents, currency, provider_status,
                verified, environment, verified_at,
                created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                auth_id,
                task_id,
                acceptance_id,
                "TEST_PROVIDER",
                f"provider_auth_{suffix}",
                2500,
                "USD",
                "AUTHORIZED",
                0,
                "TEST",
                None,
                timestamp,
                timestamp,
            ),
        )

        expect_rejection(
            "Unverified payment authorization",
            lambda: engine.create_settlement(
                conn,
                task_id=task_id,
                obligation_id=f"obl_unverified_{suffix}",
                eligibility_decision_id=eligibility_id,
                contract_acceptance_decision_id=acceptance_id,
                payment_authorization_id=auth_id,
                amount_cents=2500,
                currency="USD",
                provider="TEST_PROVIDER",
                environment="TEST",
            ),
        )

        # Make authorization valid for remaining tests.
        conn.execute(
            """
            UPDATE payment_authorizations
            SET verified = 1,
                verified_at = ?
            WHERE authorization_id = ?
            """,
            (timestamp, auth_id),
        )

        # ------------------------------------------------------------
        # Amount mismatch
        # ------------------------------------------------------------

        expect_rejection(
            "Amount mismatch",
            lambda: engine.create_settlement(
                conn,
                task_id=task_id,
                obligation_id=f"obl_amount_{suffix}",
                eligibility_decision_id=eligibility_id,
                contract_acceptance_decision_id=acceptance_id,
                payment_authorization_id=auth_id,
                amount_cents=9999,
                currency="USD",
                provider="TEST_PROVIDER",
                environment="TEST",
            ),
        )

        # ------------------------------------------------------------
        # Currency mismatch
        # ------------------------------------------------------------

        expect_rejection(
            "Currency mismatch",
            lambda: engine.create_settlement(
                conn,
                task_id=task_id,
                obligation_id=f"obl_currency_{suffix}",
                eligibility_decision_id=eligibility_id,
                contract_acceptance_decision_id=acceptance_id,
                payment_authorization_id=auth_id,
                amount_cents=2500,
                currency="AUD",
                provider="TEST_PROVIDER",
                environment="TEST",
            ),
        )

        # ------------------------------------------------------------
        # Environment mismatch
        # ------------------------------------------------------------

        expect_rejection(
            "Environment mismatch",
            lambda: engine.create_settlement(
                conn,
                task_id=task_id,
                obligation_id=f"obl_environment_{suffix}",
                eligibility_decision_id=eligibility_id,
                contract_acceptance_decision_id=acceptance_id,
                payment_authorization_id=auth_id,
                amount_cents=2500,
                currency="USD",
                provider="TEST_PROVIDER",
                environment="SANDBOX",
            ),
        )

        # ------------------------------------------------------------
        # Provider mismatch
        # ------------------------------------------------------------

        expect_rejection(
            "Provider mismatch",
            lambda: engine.create_settlement(
                conn,
                task_id=task_id,
                obligation_id=f"obl_provider_{suffix}",
                eligibility_decision_id=eligibility_id,
                contract_acceptance_decision_id=acceptance_id,
                payment_authorization_id=auth_id,
                amount_cents=2500,
                currency="USD",
                provider="WRONG_PROVIDER",
                environment="TEST",
            ),
        )

        # ------------------------------------------------------------
        # Rejected contractual acceptance
        # ------------------------------------------------------------

        rejected_event = f"rejected_evt_{suffix}"
        rejected_decision = f"rejected_dec_{suffix}"
        rejected_auth = f"rejected_auth_{suffix}"

        conn.execute(
            """
            INSERT INTO external_acceptance_events (
                event_id, task_id, source, external_event_id,
                event_type, authenticated, environment,
                observed_at, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                rejected_event,
                task_id,
                "TEST_HARNESS",
                f"rejected_external_{suffix}",
                "TEST_REJECTION",
                1,
                "TEST",
                timestamp,
                timestamp,
            ),
        )

        conn.execute(
            """
            INSERT INTO contract_acceptance_decisions (
                decision_id, task_id, acceptance_event_id,
                policy_name, policy_version, accepted,
                rejection_reasons, environment, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                rejected_decision,
                task_id,
                rejected_event,
                "test_contract_policy",
                "1",
                0,
                "Synthetic rejection",
                "TEST",
                timestamp,
            ),
        )

        conn.execute(
            """
            INSERT INTO payment_authorizations (
                authorization_id, task_id,
                contract_acceptance_decision_id,
                provider, provider_authorization_id,
                amount_cents, currency, provider_status,
                verified, environment, verified_at,
                created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                rejected_auth,
                task_id,
                rejected_decision,
                "TEST_PROVIDER",
                f"rejected_provider_auth_{suffix}",
                2500,
                "USD",
                "AUTHORIZED",
                1,
                "TEST",
                timestamp,
                timestamp,
                timestamp,
            ),
        )

        expect_rejection(
            "Rejected contractual acceptance",
            lambda: engine.create_settlement(
                conn,
                task_id=task_id,
                obligation_id=f"obl_rejected_{suffix}",
                eligibility_decision_id=eligibility_id,
                contract_acceptance_decision_id=rejected_decision,
                payment_authorization_id=rejected_auth,
                amount_cents=2500,
                currency="USD",
                provider="TEST_PROVIDER",
                environment="TEST",
            ),
        )

        # ------------------------------------------------------------
        # Create one legitimate settlement.
        # ------------------------------------------------------------

        legitimate = engine.create_settlement(
            conn,
            task_id=task_id,
            obligation_id=f"obl_valid_{suffix}",
            eligibility_decision_id=eligibility_id,
            contract_acceptance_decision_id=acceptance_id,
            payment_authorization_id=auth_id,
            amount_cents=2500,
            currency="USD",
            provider="TEST_PROVIDER",
            environment="TEST",
        )

        print("[INFO] Legitimate settlement created")

        # ------------------------------------------------------------
        # Same obligation should return the same settlement.
        # ------------------------------------------------------------

        repeated = engine.create_settlement(
            conn,
            task_id=task_id,
            obligation_id=f"obl_valid_{suffix}",
            eligibility_decision_id=eligibility_id,
            contract_acceptance_decision_id=acceptance_id,
            payment_authorization_id=auth_id,
            amount_cents=2500,
            currency="USD",
            provider="TEST_PROVIDER",
            environment="TEST",
        )

        if repeated["settlement_id"] == legitimate["settlement_id"]:
            print("[PASS] Identical settlement replay is idempotent")
            passed += 1
        else:
            print("[FAIL] Identical settlement replay changed identity")
            failed += 1

        # ------------------------------------------------------------
        # Same authorization, different obligation must fail.
        # ------------------------------------------------------------

        expect_rejection(
            "Payment authorization reuse",
            lambda: engine.create_settlement(
                conn,
                task_id=task_id,
                obligation_id=f"obl_second_{suffix}",
                eligibility_decision_id=eligibility_id,
                contract_acceptance_decision_id=acceptance_id,
                payment_authorization_id=auth_id,
                amount_cents=2500,
                currency="USD",
                provider="TEST_PROVIDER",
                environment="TEST",
            ),
        )

        # ------------------------------------------------------------
        # Illegal direct transition.
        # ------------------------------------------------------------

        expect_rejection(
            "PAYMENT_AUTHORIZED -> REVENUE_RECORDED",
            lambda: engine.transition_settlement(
                conn,
                legitimate["settlement_id"],
                "REVENUE_RECORDED",
                reason="Illegal shortcut.",
            ),
        )

        print("\nTEST RESULTS")
        print("PASS:", passed)
        print("FAIL:", failed)

    finally:
        conn.rollback()
        print("ROLLBACK: COMPLETE")


with db.get_db() as conn:
    print(
        "PERSISTED CANONICAL SETTLEMENTS:",
        conn.execute(
            "SELECT COUNT(*) FROM canonical_settlements"
        ).fetchone()[0],
    )

    print(
        "PERSISTED REVENUE:",
        conn.execute(
            "SELECT COUNT(*) FROM revenue_ledger"
        ).fetchone()[0],
    )

    print(
        "integrity_check:",
        conn.execute("PRAGMA integrity_check").fetchone()[0],
    )

    print(
        "foreign_key_errors:",
        len(conn.execute("PRAGMA foreign_key_check").fetchall()),
    )
