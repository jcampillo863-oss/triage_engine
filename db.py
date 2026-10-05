import sqlite3
import os
from contextlib import contextmanager


DB_DIR = os.path.join(os.path.dirname(__file__), "data")
DB_PATH = os.path.join(DB_DIR, "settlement.db")

DB_TIMEOUT_SECONDS = 10
DB_BUSY_TIMEOUT_MS = 10000


@contextmanager
def get_db():
    os.makedirs(DB_DIR, exist_ok=True)

    conn = sqlite3.connect(
        DB_PATH,
        timeout=DB_TIMEOUT_SECONDS
    )

    try:
        # Existing concurrency mode.
        conn.execute("PRAGMA journal_mode=WAL;")

        # SQLite foreign keys must be enabled per connection.
        conn.execute("PRAGMA foreign_keys=ON;")

        # Allow concurrent processes a bounded period to release locks.
        conn.execute(
            f"PRAGMA busy_timeout={DB_BUSY_TIMEOUT_MS};"
        )

        # Existing code expects sqlite3.Row-style name access in places.
        conn.row_factory = sqlite3.Row

        yield conn

    finally:
        conn.close()


def init_db():
    with get_db() as conn:
        cursor = conn.cursor()

        # 1. Raw Evidence
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS raw_evidence (
                evidence_id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                git_commit_hash TEXT,
                pytest_exit_code INTEGER,
                syntax_valid INTEGER NOT NULL,
                compilation_valid INTEGER NOT NULL,
                diff_policy_passed INTEGER NOT NULL,
                raw_payload TEXT,
                created_at TEXT NOT NULL
            );
        """)

        # 2. Validation Results
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS validation_results (
                result_id INTEGER PRIMARY KEY AUTOINCREMENT,
                validator_id TEXT NOT NULL,
                evidence_id TEXT NOT NULL,
                status TEXT NOT NULL,
                checks_json TEXT NOT NULL,
                confidence REAL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(evidence_id)
                    REFERENCES raw_evidence(evidence_id)
            );
        """)

        # 3. Acceptance Decisions
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS acceptance_decisions (
                decision_id TEXT PRIMARY KEY,
                evidence_id TEXT NOT NULL,
                policy_name TEXT NOT NULL,
                accepted INTEGER NOT NULL,
                rejection_reasons TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(evidence_id)
                    REFERENCES raw_evidence(evidence_id)
            );
        """)

        # 4. Settlement Records
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS settlement_records (
                settlement_id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                decision_id TEXT,
                amount_cents INTEGER NOT NULL,
                currency TEXT NOT NULL DEFAULT 'USD',
                state TEXT NOT NULL,
                provider_tx_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
        """)

        conn.commit()

    print(
        f"Database substrate initialized at {DB_PATH} "
        "(WAL mode active, foreign keys enforced)."
    )


if __name__ == "__main__":
    init_db()
