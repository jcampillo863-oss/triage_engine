import sqlite3
from datetime import datetime, timezone

from canonical_settlement_engine import (
    record_revenue,
)


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def make_test_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

    conn.executescript(
        """
        CREATE TABLE canonical_settlements (
            settlement_id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL,
            obligation_id TEXT NOT NULL,
            contract_acceptance_decision_id TEXT NOT NULL,
            payment_authorization_id TEXT NOT NULL,
            amount_cents INTEGER NOT NULL,
            currency TEXT NOT NULL,
            state TEXT NOT NULL,
            provider TEXT NOT NULL,
            provider_capture_id TEXT,
            environment TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE canonical_settlement_journal (
            journal_id INTEGER PRIMARY KEY AUTOINCREMENT,
            settlement_id TEXT NOT NULL,
            from_state TEXT,
            to_state TEXT NOT NULL,
            reason TEXT NOT NULL,
            evidence_reference TEXT,
            environment TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE revenue_ledger (
            ledger_id TEXT PRIMARY KEY,
            settlement_id TEXT NOT NULL,
            task_id TEXT NOT NULL,
            provider TEXT NOT NULL,
            provider_tx_id TEXT NOT NULL,
            amount_cents INTEGER NOT NULL,
            currency TEXT NOT NULL,
            environment TEXT NOT NULL,
            recorded_at TEXT NOT NULL,

            UNIQUE(provider, provider_tx_id),
            UNIQUE(settlement_id)
        );
        """
    )

    return conn


def seed_settlement(
    conn,
    *,
    state,
    provider_capture_id=None,
):
    timestamp = now_iso()

    conn.execute(
        """
        INSERT INTO canonical_settlements (
            settlement_id,
            task_id,
            obligation_id,
            contract_acceptance_decision_id,
            payment_authorization_id,
            amount_cents,
            currency,
            state,
            provider,
            provider_capture_id,
            environment,
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "settlement-test-001",
            "task-test-001",
            "obligation-test-001",
            "decision-test-001",
            "authorization-test-001",
            100,
            "AUD",
            state,
            "paypal",
            provider_capture_id,
            "TEST",
            timestamp,
            timestamp,
        ),
    )

    conn.commit()


def test_revenue_rejected_before_confirmation():
    conn = make_test_db()

    try:
        seed_settlement(
            conn,
            state="PAYMENT_AUTHORIZED",
        )

        try:
            record_revenue(
                conn,
                "settlement-test-001",
                ledger_id="ledger-test-001",
                reason="Test revenue recording.",
                evidence_reference="test",
            )
        except ValueError as exc:
            assert (
                "PROVIDER_CONFIRMED"
                in str(exc)
            )
        else:
            raise AssertionError(
                "Revenue was recorded before provider confirmation."
            )

        count = conn.execute(
            "SELECT COUNT(*) FROM revenue_ledger"
        ).fetchone()[0]

        assert count == 0

        print(
            "PRE-CONFIRMATION REVENUE GUARD: PASSED"
        )
        print("LEDGER ROWS:", count)

    finally:
        conn.close()


def test_confirmed_revenue_recording():
    conn = make_test_db()

    try:
        seed_settlement(
            conn,
            state="PROVIDER_CONFIRMED",
            provider_capture_id="FAKE-CAPTURE-001",
        )

        result = record_revenue(
            conn,
            "settlement-test-001",
            ledger_id="ledger-test-001",
            reason="Provider-confirmed revenue.",
            evidence_reference="FAKE-CAPTURE-001",
        )

        assert result["state"] == "REVENUE_RECORDED"
        assert conn.in_transaction is True

        ledger = conn.execute(
            """
            SELECT *
            FROM revenue_ledger
            WHERE settlement_id = ?
            """,
            ("settlement-test-001",),
        ).fetchone()

        assert ledger is not None
        assert ledger["ledger_id"] == "ledger-test-001"
        assert ledger["provider"] == "paypal"
        assert ledger["provider_tx_id"] == "FAKE-CAPTURE-001"
        assert ledger["amount_cents"] == 100
        assert ledger["currency"] == "AUD"
        assert ledger["environment"] == "TEST"

        journal = conn.execute(
            """
            SELECT from_state, to_state
            FROM canonical_settlement_journal
            WHERE settlement_id = ?
            """,
            ("settlement-test-001",),
        ).fetchall()

        assert len(journal) == 1
        assert journal[0]["from_state"] == "PROVIDER_CONFIRMED"
        assert journal[0]["to_state"] == "REVENUE_RECORDED"

        conn.commit()

        print("CONFIRMED REVENUE RECORDING: PASSED")
        print("FINAL STATE:", result["state"])
        print("LEDGER ROWS: 1")
        print("AMOUNT: 100 cents AUD")
        print("PROVIDER TX MATCH: True")

    finally:
        conn.close()


def test_revenue_replay_is_idempotent():
    conn = make_test_db()

    try:
        seed_settlement(
            conn,
            state="PROVIDER_CONFIRMED",
            provider_capture_id="FAKE-CAPTURE-001",
        )

        first = record_revenue(
            conn,
            "settlement-test-001",
            ledger_id="ledger-test-001",
            reason="Initial revenue recording.",
            evidence_reference="FAKE-CAPTURE-001",
        )

        assert first["state"] == "REVENUE_RECORDED"
        conn.commit()

        second = record_revenue(
            conn,
            "settlement-test-001",
            ledger_id="ledger-test-001",
            reason="Replay.",
            evidence_reference="FAKE-CAPTURE-001",
        )

        assert second["state"] == "REVENUE_RECORDED"

        ledger_count = conn.execute(
            """
            SELECT COUNT(*)
            FROM revenue_ledger
            WHERE settlement_id = ?
            """,
            ("settlement-test-001",),
        ).fetchone()[0]

        journal_count = conn.execute(
            """
            SELECT COUNT(*)
            FROM canonical_settlement_journal
            WHERE settlement_id = ?
            """,
            ("settlement-test-001",),
        ).fetchone()[0]

        assert ledger_count == 1
        assert journal_count == 1

        print("REVENUE IDEMPOTENT REPLAY: PASSED")
        print("LEDGER ROWS:", ledger_count)
        print("JOURNAL ENTRIES:", journal_count)
        print("DUPLICATE REVENUE: False")

    finally:
        conn.close()


if __name__ == "__main__":
    test_revenue_rejected_before_confirmation()
    test_confirmed_revenue_recording()
    test_revenue_replay_is_idempotent()

    print("CANONICAL REVENUE RECORDING V1: PASSED")