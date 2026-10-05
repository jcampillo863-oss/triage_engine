import uuid
from datetime import datetime, timezone

import db
import canonical_settlement_engine as engine
from eligibility_test_support import seed_test_eligibility


def now():
    return datetime.now(timezone.utc).isoformat()


suffix = uuid.uuid4().hex[:8]

event_id = f"test_evt_{suffix}"
acceptance_id = f"test_accept_{suffix}"
authorization_id = f"test_auth_{suffix}"
task_id = f"test_task_{suffix}"
obligation_id = f"test_obligation_{suffix}"

with db.get_db() as conn:
    try:
        conn.execute("BEGIN")

        timestamp = now()

        conn.execute(
            """
            INSERT INTO external_acceptance_events (
                event_id,
                task_id,
                source,
                external_event_id,
                event_type,
                authenticated,
                environment,
                observed_at,
                created_at
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
                decision_id,
                task_id,
                acceptance_event_id,
                policy_name,
                policy_version,
                accepted,
                environment,
                created_at
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
                acceptance_id,
                "TEST_PROVIDER",
                f"provider_auth_{suffix}",
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

        settlement = engine.create_settlement(
            conn,
            task_id=task_id,
            obligation_id=obligation_id,
            eligibility_decision_id=eligibility_id,
            contract_acceptance_decision_id=acceptance_id,
            payment_authorization_id=authorization_id,
            amount_cents=2500,
            currency="USD",
            provider="TEST_PROVIDER",
            environment="TEST",
        )

        print("CREATED:", settlement["state"])

        settlement = engine.transition_settlement(
            conn,
            settlement["settlement_id"],
            "CAPTURE_REQUESTED",
            reason="Test capture request.",
        )

        print("CAPTURE:", settlement["state"])

        settlement = engine.transition_settlement(
            conn,
            settlement["settlement_id"],
            "OUTCOME_UNKNOWN",
            reason="Synthetic timeout.",
        )

        print("UNKNOWN:", settlement["state"])

        settlement = engine.transition_settlement(
            conn,
            settlement["settlement_id"],
            "RECONCILING",
            reason="Synthetic reconciliation.",
        )

        print("RECONCILING:", settlement["state"])

        settlement = engine.transition_settlement(
            conn,
            settlement["settlement_id"],
            "PROVIDER_CONFIRMED",
            reason="Synthetic provider confirmation.",
            provider_capture_id=f"test_capture_{suffix}",
        )

        print("CONFIRMED:", settlement["state"])

        # Direct declaration of REVENUE_RECORDED must now be impossible.
        try:
            engine.transition_settlement(
                conn,
                settlement["settlement_id"],
                "REVENUE_RECORDED",
                reason="Illegal direct revenue transition.",
            )
            print("DIRECT REVENUE TRANSITION TEST: FAILED")
        except ValueError as exc:
            print("DIRECT REVENUE TRANSITION TEST: PASSED")
            print(" ", exc)

        # Revenue must instead be finalized through the accounting
        # operation that writes the ledger.
        settlement = engine.record_revenue(
            conn,
            settlement["settlement_id"],
            ledger_id=f"ledger_{suffix}",
            reason="Synthetic ledger completion.",
        )

        print("FINAL:", settlement["state"])
        journal_count = conn.execute(
            """
            SELECT COUNT(*)
            FROM canonical_settlement_journal
            WHERE settlement_id = ?
            """,
            (settlement["settlement_id"],),
        ).fetchone()[0]

        print("JOURNAL EVENTS:", journal_count)

        try:
            engine.transition_settlement(
                conn,
                settlement["settlement_id"],
                "CAPTURE_REQUESTED",
                reason="This must fail.",
            )
            print("ILLEGAL TRANSITION TEST: FAILED")
        except ValueError as exc:
            print("ILLEGAL TRANSITION TEST: PASSED")
            print(" ", exc)

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
        "PERSISTED CANONICAL JOURNAL EVENTS:",
        conn.execute(
            "SELECT COUNT(*) FROM canonical_settlement_journal"
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
