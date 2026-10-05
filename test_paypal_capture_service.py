import sqlite3
import requests
from datetime import datetime, timezone

from paypal_capture_service import (
    CaptureServiceError,
    execute_capture,
    prepare_capture,
    reconcile_capture,
)

from canonical_settlement_engine import (
    get_settlement,
    transition_settlement,
)

def now_iso():
    return datetime.now(timezone.utc).isoformat()


def make_test_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

    conn.execute("PRAGMA foreign_keys = ON")

    conn.executescript(
        """
        CREATE TABLE contract_acceptance_decisions (
            decision_id TEXT PRIMARY KEY
        );

        CREATE TABLE payment_authorizations (
            authorization_id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL,
            contract_acceptance_decision_id TEXT,
            provider TEXT NOT NULL,
            provider_authorization_id TEXT NOT NULL,
            amount_cents INTEGER NOT NULL CHECK (amount_cents >= 0),
            currency TEXT NOT NULL,
            provider_status TEXT NOT NULL,
            verified INTEGER NOT NULL CHECK (verified IN (0, 1)),
            environment TEXT NOT NULL
                CHECK (environment IN ('TEST', 'SANDBOX', 'PRODUCTION')),
            verified_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,

            FOREIGN KEY(contract_acceptance_decision_id)
                REFERENCES contract_acceptance_decisions(decision_id),

            UNIQUE(provider, provider_authorization_id)
        );

        CREATE TABLE canonical_settlements (
            settlement_id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL,
            obligation_id TEXT NOT NULL,
            contract_acceptance_decision_id TEXT NOT NULL,
            payment_authorization_id TEXT NOT NULL,
            amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
            currency TEXT NOT NULL,

            state TEXT NOT NULL CHECK (
                state IN (
                    'PAYMENT_AUTHORIZED',
                    'CAPTURE_REQUESTED',
                    'OUTCOME_UNKNOWN',
                    'RECONCILING',
                    'PROVIDER_CONFIRMED',
                    'REVENUE_RECORDED',
                    'FAILED',
                    'MANUAL_REVIEW_REQUIRED'
                )
            ),

            provider TEXT NOT NULL,
            provider_capture_id TEXT,

            environment TEXT NOT NULL
                CHECK (environment IN ('TEST', 'SANDBOX', 'PRODUCTION')),

            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,

            FOREIGN KEY(contract_acceptance_decision_id)
                REFERENCES contract_acceptance_decisions(decision_id),

            FOREIGN KEY(payment_authorization_id)
                REFERENCES payment_authorizations(authorization_id),

            UNIQUE(obligation_id),
            UNIQUE(payment_authorization_id)
        );

        CREATE TABLE canonical_settlement_journal (
            journal_id INTEGER PRIMARY KEY AUTOINCREMENT,
            settlement_id TEXT NOT NULL,
            from_state TEXT,
            to_state TEXT NOT NULL,
            reason TEXT NOT NULL,
            evidence_reference TEXT,
            environment TEXT NOT NULL
                CHECK (environment IN ('TEST', 'SANDBOX', 'PRODUCTION')),
            created_at TEXT NOT NULL
        );

        CREATE TABLE revenue_ledger (
            ledger_id TEXT PRIMARY KEY,
            settlement_id TEXT NOT NULL,
            task_id TEXT NOT NULL,
            provider TEXT NOT NULL,
            provider_tx_id TEXT NOT NULL,
            amount_cents INTEGER NOT NULL CHECK (amount_cents >= 0),
            currency TEXT NOT NULL,
            environment TEXT NOT NULL
                CHECK (environment IN ('TEST', 'SANDBOX', 'PRODUCTION')),
            recorded_at TEXT NOT NULL,

            UNIQUE(provider, provider_tx_id),
            UNIQUE(settlement_id)
        );
        """
    )

    return conn


def seed_payment_authorized_settlement(conn):
    timestamp = now_iso()

    conn.execute(
        """
        INSERT INTO contract_acceptance_decisions (
            decision_id
        )
        VALUES (?)
        """,
        ("decision-test-001",),
    )

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
            "authorization-test-001",
            "task-test-001",
            "decision-test-001",
            "paypal",
            "PAYPAL-AUTH-FAKE-001",
            100,
            "AUD",
            "CREATED",
            1,
            "TEST",
            timestamp,
            timestamp,
            timestamp,
        ),
    )

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
            "PAYMENT_AUTHORIZED",
            "paypal",
            None,
            "TEST",
            timestamp,
            timestamp,
        ),
    )

    conn.commit()


def test_prepare_capture():
    conn = make_test_db()

    try:
        seed_payment_authorized_settlement(conn)

        result = prepare_capture(
            conn,
            "settlement-test-001",
        )

        assert result.state == "CAPTURE_REQUESTED"
        assert conn.in_transaction is True

        row = conn.execute(
            """
            SELECT state
            FROM canonical_settlements
            WHERE settlement_id = ?
            """,
            ("settlement-test-001",),
        ).fetchone()

        assert row["state"] == "CAPTURE_REQUESTED"

        journal = conn.execute(
            """
            SELECT from_state, to_state
            FROM canonical_settlement_journal
            WHERE settlement_id = ?
            ORDER BY journal_id
            """,
            ("settlement-test-001",),
        ).fetchall()

        assert len(journal) == 1
        assert journal[0]["from_state"] == "PAYMENT_AUTHORIZED"
        assert journal[0]["to_state"] == "CAPTURE_REQUESTED"

        conn.commit()

        assert conn.in_transaction is False

        print("PREPARE CAPTURE TEST: PASSED")
        print("STATE: CAPTURE_REQUESTED")
        print("JOURNAL ENTRIES:", len(journal))
        print("PROVIDER CALLED: False")

    finally:
        conn.close()


def test_provider_blocked_before_commit():
    conn = make_test_db()
    provider_calls = []

    def fake_capture(
        authorization_id,
        *,
        request_id,
    ):
        provider_calls.append(
            (authorization_id, request_id)
        )

        return {
            "id": "FAKE-CAPTURE-001",
            "status": "COMPLETED",
        }

    try:
        seed_payment_authorized_settlement(conn)

        result = prepare_capture(
            conn,
            "settlement-test-001",
        )

        assert result.state == "CAPTURE_REQUESTED"
        assert conn.in_transaction is True

        try:
            execute_capture(
                conn,
                "settlement-test-001",
                capture_fn=fake_capture,
            )

        except CaptureServiceError as exc:
            assert "must be committed" in str(exc)

        else:
            raise AssertionError(
                "Provider execution was not blocked."
            )

        assert provider_calls == []

        row = conn.execute(
            """
            SELECT state
            FROM canonical_settlements
            WHERE settlement_id = ?
            """,
            ("settlement-test-001",),
        ).fetchone()

        assert row["state"] == "CAPTURE_REQUESTED"

        print("PRE-COMMIT PROVIDER GUARD: PASSED")
        print("PROVIDER CALLS:", len(provider_calls))

    finally:
        conn.close()


def test_completed_capture_after_commit():
    conn = make_test_db()
    provider_calls = []

    def fake_capture(
        authorization_id,
        *,
        request_id,
    ):
        provider_calls.append(
            (authorization_id, request_id)
        )

        return {
            "id": "FAKE-CAPTURE-001",
            "status": "COMPLETED",
        }

    try:
        seed_payment_authorized_settlement(conn)

        prepared = prepare_capture(
            conn,
            "settlement-test-001",
        )

        assert prepared.state == "CAPTURE_REQUESTED"

        # Persist CAPTURE_REQUESTED before provider contact.
        conn.commit()

        assert conn.in_transaction is False

        result = execute_capture(
            conn,
            "settlement-test-001",
            capture_fn=fake_capture,
        )

        assert len(provider_calls) == 1

        authorization_id, request_id = provider_calls[0]

        assert authorization_id == "PAYPAL-AUTH-FAKE-001"
        assert request_id.startswith("cap-")
        assert len(request_id) == 36

        assert result.state == "PROVIDER_CONFIRMED"
        assert result.provider_capture_id == "FAKE-CAPTURE-001"

        row = conn.execute(
            """
            SELECT state, provider_capture_id
            FROM canonical_settlements
            WHERE settlement_id = ?
            """,
            ("settlement-test-001",),
        ).fetchone()

        assert row["state"] == "PROVIDER_CONFIRMED"
        assert row["provider_capture_id"] == "FAKE-CAPTURE-001"

        journal = conn.execute(
            """
            SELECT from_state, to_state
            FROM canonical_settlement_journal
            WHERE settlement_id = ?
            ORDER BY journal_id
            """,
            ("settlement-test-001",),
        ).fetchall()

        assert len(journal) == 2
        assert journal[0]["from_state"] == "PAYMENT_AUTHORIZED"
        assert journal[0]["to_state"] == "CAPTURE_REQUESTED"
        assert journal[1]["from_state"] == "CAPTURE_REQUESTED"
        assert journal[1]["to_state"] == "PROVIDER_CONFIRMED"

        assert conn.in_transaction is True

        conn.commit()

        assert conn.in_transaction is False

        print("COMPLETED CAPTURE TEST: PASSED")
        print("PROVIDER CALLS:", len(provider_calls))
        print("STATE: PROVIDER_CONFIRMED")
        print("CAPTURE ID:", result.provider_capture_id)
        print("JOURNAL ENTRIES:", len(journal))

    finally:
        conn.close()

def test_timeout_becomes_outcome_unknown():
    conn = make_test_db()
    provider_calls = []

    def fake_timeout(
        authorization_id,
        *,
        request_id,
    ):
        provider_calls.append(
            (authorization_id, request_id)
        )

        raise requests.Timeout(
            "Simulated lost provider response."
        )

    try:
        seed_payment_authorized_settlement(conn)

        prepared = prepare_capture(
            conn,
            "settlement-test-001",
        )

        assert prepared.state == "CAPTURE_REQUESTED"

        # The provider may only be contacted after this commit.
        conn.commit()

        assert conn.in_transaction is False

        result = execute_capture(
            conn,
            "settlement-test-001",
            capture_fn=fake_timeout,
        )

        assert len(provider_calls) == 1
        assert result.state == "OUTCOME_UNKNOWN"
        assert result.provider_capture_id is None

        row = conn.execute(
            """
            SELECT state, provider_capture_id
            FROM canonical_settlements
            WHERE settlement_id = ?
            """,
            ("settlement-test-001",),
        ).fetchone()

        assert row["state"] == "OUTCOME_UNKNOWN"
        assert row["provider_capture_id"] is None

        journal = conn.execute(
            """
            SELECT from_state, to_state
            FROM canonical_settlement_journal
            WHERE settlement_id = ?
            ORDER BY journal_id
            """,
            ("settlement-test-001",),
        ).fetchall()

        assert len(journal) == 2
        assert journal[0]["from_state"] == "PAYMENT_AUTHORIZED"
        assert journal[0]["to_state"] == "CAPTURE_REQUESTED"
        assert journal[1]["from_state"] == "CAPTURE_REQUESTED"
        assert journal[1]["to_state"] == "OUTCOME_UNKNOWN"

        # Caller owns persistence of the ambiguity record.
        assert conn.in_transaction is True

        conn.commit()

        assert conn.in_transaction is False

        print("AMBIGUOUS TIMEOUT TEST: PASSED")
        print("PROVIDER CALLS:", len(provider_calls))
        print("STATE: OUTCOME_UNKNOWN")
        print("CAPTURE ID: None")
        print("BLIND RETRY: False")

    finally:
        conn.close()

def test_reconciliation_outcomes():
    cases = [
        (
            "COMPLETED",
            {
                "outcome": "COMPLETED",
                "capture_id": "FAKE-RECON-CAPTURE-001",
            },
            "PROVIDER_CONFIRMED",
            "FAKE-RECON-CAPTURE-001",
        ),
        (
            "NOT_CAPTURED",
            {
                "outcome": "NOT_CAPTURED",
            },
            "RECONCILING",
            None,
        ),
        (
            "UNRESOLVED",
            {
                "outcome": "UNRESOLVED",
            },
            "MANUAL_REVIEW_REQUIRED",
            None,
        ),
    ]

    for (
        case_name,
        observation,
        expected_state,
        expected_capture_id,
    ) in cases:
        conn = make_test_db()
        observer_calls = []

        def fake_observer(provider_authorization_id):
            observer_calls.append(
                provider_authorization_id
            )
            return observation

        try:
            seed_payment_authorized_settlement(conn)

            prepared = prepare_capture(
                conn,
                "settlement-test-001",
            )

            assert prepared.state == "CAPTURE_REQUESTED"
            conn.commit()

            # Simulate the already-tested ambiguous provider call.
            unknown = execute_capture(
                conn,
                "settlement-test-001",
                capture_fn=lambda authorization_id, request_id: (
                    (_ for _ in ()).throw(
                        requests.Timeout(
                            "Simulated ambiguous capture."
                        )
                    )
                ),
            )

            assert unknown.state == "OUTCOME_UNKNOWN"
            conn.commit()

            result = reconcile_capture(
                conn,
                "settlement-test-001",
                observe_fn=fake_observer,
            )

            assert len(observer_calls) == 1
            assert (
                observer_calls[0]
                == "PAYPAL-AUTH-FAKE-001"
            )

            assert result.state == expected_state
            assert (
                result.provider_capture_id
                == expected_capture_id
            )

            row = conn.execute(
                """
                SELECT state, provider_capture_id
                FROM canonical_settlements
                WHERE settlement_id = ?
                """,
                ("settlement-test-001",),
            ).fetchone()

            assert row["state"] == expected_state
            assert (
                row["provider_capture_id"]
                == expected_capture_id
            )

            journal = conn.execute(
                """
                SELECT from_state, to_state
                FROM canonical_settlement_journal
                WHERE settlement_id = ?
                ORDER BY journal_id
                """,
                ("settlement-test-001",),
            ).fetchall()

            expected_journal_count = (
                3
                if case_name == "NOT_CAPTURED"
                else 4
            )

            assert len(journal) == expected_journal_count

            assert journal[0]["to_state"] == (
                "CAPTURE_REQUESTED"
            )
            assert journal[1]["to_state"] == (
                "OUTCOME_UNKNOWN"
            )
            assert journal[2]["to_state"] == (
                "RECONCILING"
            )
            if case_name != "NOT_CAPTURED":
                assert journal[3]["to_state"] == (
                    expected_state
                )
            # Final reconciliation result remains caller-owned.
            if case_name == "NOT_CAPTURED":
                assert conn.in_transaction is False
            else:
                assert conn.in_transaction is True

            conn.commit()
            print(
                f"RECONCILIATION {case_name}: PASSED"
            )
            print("FINAL STATE:", expected_state)
            print(
                "CAPTURE ID:",
                expected_capture_id,
            )
            print(
                "OBSERVER CALLS:",
                len(observer_calls),
            )

        finally:
            conn.close()
def test_reconciliation_restart_resume():
    conn = make_test_db()

    try:
        seed_payment_authorized_settlement(conn)

        prepare_capture(
            conn,
            "settlement-test-001",
        )
        conn.commit()

        def timeout_capture(*args, **kwargs):
            raise requests.Timeout(
                "Simulated provider timeout."
            )

        result = execute_capture(
            conn,
            "settlement-test-001",
            capture_fn=timeout_capture,
        )

        assert result.state == "OUTCOME_UNKNOWN"
        conn.commit()

        # Simulate the first reconciliation process:
        # RECONCILING becomes durable, then the
        # process dies before provider observation.
        transition_settlement(
            conn,
            "settlement-test-001",
            "RECONCILING",
            reason="simulated pre-observation crash",
            evidence_reference="restart_resume_test",
        )

        conn.commit()

        settlement = get_settlement(
            conn,
            "settlement-test-001",
        )

        assert settlement["state"] == "RECONCILING"

        observer_calls = []

        def fake_observer(provider_authorization_id):
            observer_calls.append(
                provider_authorization_id
            )

            return {
                "outcome": "COMPLETED",
                "capture_id": "FAKE-RESTART-CAPTURE-001",
            }

        # Simulate the restarted process.
        result = reconcile_capture(
            conn,
            "settlement-test-001",
            observe_fn=fake_observer,
        )

        assert result.state == "PROVIDER_CONFIRMED"
        assert (
            result.provider_capture_id
            == "FAKE-RESTART-CAPTURE-001"
        )

        assert observer_calls == [
            "PAYPAL-AUTH-FAKE-001"
        ]

        print(
            "RECONCILIATION RESTART/RESUME: PASSED"
        )
        print("FINAL STATE:", result.state)
        print(
            "CAPTURE ID:",
            result.provider_capture_id,
        )
        print(
            "OBSERVER CALLS:",
            len(observer_calls),
        )

        conn.commit()

    finally:
        conn.close()

def test_delayed_capture_visibility():
    conn = make_test_db()

    try:
        seed_payment_authorized_settlement(conn)

        capture_calls = []

        def ambiguous_capture(
            authorization_id,
            *,
            request_id,
        ):
            capture_calls.append(
                (authorization_id, request_id)
            )

            raise requests.Timeout(
                "Simulated lost provider response."
            )

        prepare_capture(
            conn,
            "settlement-test-001",
        )
        conn.commit()

        # The financial operation is attempted exactly once.
        result = execute_capture(
            conn,
            "settlement-test-001",
            capture_fn=ambiguous_capture,
        )

        assert result.state == "OUTCOME_UNKNOWN"
        assert len(capture_calls) == 1
        conn.commit()

        first_observer_calls = []

        def first_observer(provider_authorization_id):
            first_observer_calls.append(
                provider_authorization_id
            )

            return {
                "outcome": "NOT_CAPTURED",
            }

        # First read-only observation:
        # provider does not show the capture yet.
        result = reconcile_capture(
            conn,
            "settlement-test-001",
            observe_fn=first_observer,
        )

        assert result.state == "RECONCILING"
        assert result.provider_capture_id is None
        assert len(first_observer_calls) == 1

        # No second financial operation occurred.
        assert len(capture_calls) == 1

        second_observer_calls = []

        def second_observer(provider_authorization_id):
            second_observer_calls.append(
                provider_authorization_id
            )

            return {
                "outcome": "COMPLETED",
                "capture_id": "FAKE-DELAYED-CAPTURE-001",
            }

        # Later read-only observation:
        # provider visibility has caught up.
        result = reconcile_capture(
            conn,
            "settlement-test-001",
            observe_fn=second_observer,
        )

        assert result.state == "PROVIDER_CONFIRMED"
        assert (
            result.provider_capture_id
            == "FAKE-DELAYED-CAPTURE-001"
        )

        assert len(second_observer_calls) == 1

        # Critical invariant:
        # reconciliation never repeated capture.
        assert len(capture_calls) == 1

        journal = conn.execute(
            """
            SELECT to_state
            FROM canonical_settlement_journal
            WHERE settlement_id = ?
            ORDER BY journal_id
            """,
            ("settlement-test-001",),
        ).fetchall()

        assert [
            row["to_state"]
            for row in journal
        ] == [
            "CAPTURE_REQUESTED",
            "OUTCOME_UNKNOWN",
            "RECONCILING",
            "PROVIDER_CONFIRMED",
        ]

        print("DELAYED CAPTURE VISIBILITY: PASSED")
        print("CAPTURE ATTEMPTS:", len(capture_calls))
        print(
            "FIRST OBSERVATION:",
            "NOT_CAPTURED",
        )
        print(
            "SECOND OBSERVATION:",
            "COMPLETED",
        )
        print("FINAL STATE:", result.state)
        print(
            "CAPTURE ID:",
            result.provider_capture_id,
        )
        print("BLIND RETRY: False")

        conn.commit()

    finally:
        conn.close()

if __name__ == "__main__":
    test_prepare_capture()
    test_provider_blocked_before_commit()
    test_completed_capture_after_commit()
    test_timeout_becomes_outcome_unknown()
    test_reconciliation_outcomes()
    test_reconciliation_restart_resume()
    test_delayed_capture_visibility()