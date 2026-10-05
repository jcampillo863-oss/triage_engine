import uuid
from datetime import datetime, timezone

import db
import canonical_settlement_engine as engine
from eligibility_test_support import seed_test_eligibility


def now():
    return datetime.now(timezone.utc).isoformat()


suffix = uuid.uuid4().hex[:8]

task_id = f"ledger_task_{suffix}"
event_id = f"ledger_evt_{suffix}"
acceptance_id = f"ledger_accept_{suffix}"
authorization_id = f"ledger_auth_{suffix}"
obligation_id = f"ledger_obligation_{suffix}"
ledger_id = f"ledger_{suffix}"


with db.get_db() as conn:
    try:
        conn.execute("BEGIN")
        timestamp = now()

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

        settlement = engine.transition_settlement(
            conn,
            settlement["settlement_id"],
            "CAPTURE_REQUESTED",
            reason="Synthetic capture request.",
        )

        settlement = engine.transition_settlement(
            conn,
            settlement["settlement_id"],
            "PROVIDER_CONFIRMED",
            reason="Synthetic provider confirmation.",
            provider_capture_id=f"capture_{suffix}",
        )

        print("BEFORE LEDGER:", settlement["state"])

        settlement = engine.record_revenue(
            conn,
            settlement["settlement_id"],
            ledger_id=ledger_id,
            reason="Revenue ledger entry written.",
        )

        print("AFTER LEDGER:", settlement["state"])

        ledger = conn.execute(
            """
            SELECT *
            FROM revenue_ledger
            WHERE settlement_id = ?
            """,
            (settlement["settlement_id"],),
        ).fetchone()

        print("LEDGER EXISTS:", ledger is not None)
        print("LEDGER AMOUNT:", ledger["amount_cents"])
        print("LEDGER CURRENCY:", ledger["currency"])

        # Idempotency test.
        settlement_again = engine.record_revenue(
            conn,
            settlement["settlement_id"],
            ledger_id=ledger_id,
            reason="Duplicate call must not duplicate revenue.",
        )

        ledger_count = conn.execute(
            """
            SELECT COUNT(*)
            FROM revenue_ledger
            WHERE settlement_id = ?
            """,
            (settlement["settlement_id"],),
        ).fetchone()[0]

        print("SECOND CALL STATE:", settlement_again["state"])
        print("LEDGER ROW COUNT:", ledger_count)

    finally:
        conn.rollback()
        print("ROLLBACK: COMPLETE")


with db.get_db() as conn:
    print(
        "PERSISTED SETTLEMENTS:",
        conn.execute(
            "SELECT COUNT(*) FROM canonical_settlements"
        ).fetchone()[0],
    )

    print(
        "PERSISTED LEDGER ROWS:",
        conn.execute(
            "SELECT COUNT(*) FROM revenue_ledger"
        ).fetchone()[0],
    )

    print(
        "PERSISTED JOURNAL EVENTS:",
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
