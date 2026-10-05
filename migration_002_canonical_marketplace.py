from datetime import datetime, timezone
import db


MIGRATION_VERSION = 2
MIGRATION_NAME = "canonical_marketplace_foundation"


def apply_migration():
    print(f"[MIGRATION {MIGRATION_VERSION:03}] Canonical Marketplace Foundation")
    print(f"[DB] {db.DB_PATH}")

    with db.get_db() as conn:
        existing = conn.execute(
            "SELECT version FROM schema_meta WHERE version = ?",
            (MIGRATION_VERSION,)
        ).fetchone()

        if existing:
            print(f"[SKIP] Migration {MIGRATION_VERSION:03} already applied.")
            return

        # Technical-policy conclusions.
        conn.execute("""
            CREATE TABLE technical_decisions (
                decision_id TEXT PRIMARY KEY,
                evidence_id TEXT NOT NULL,
                validation_result_id INTEGER,
                policy_name TEXT NOT NULL,
                policy_version TEXT NOT NULL,
                accepted INTEGER NOT NULL CHECK (accepted IN (0, 1)),
                rejection_reasons TEXT,
                environment TEXT NOT NULL
                    CHECK (environment IN ('TEST', 'SANDBOX', 'PRODUCTION')),
                created_at TEXT NOT NULL,

                FOREIGN KEY(evidence_id)
                    REFERENCES raw_evidence(evidence_id),
                FOREIGN KEY(validation_result_id)
                    REFERENCES validation_results(result_id),

                UNIQUE(evidence_id, policy_name, policy_version)
            )
        """)

        # Externally observed contractual-acceptance events.
        conn.execute("""
            CREATE TABLE external_acceptance_events (
                event_id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                source TEXT NOT NULL,
                external_event_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                repository TEXT,
                pull_request_url TEXT,
                git_commit_hash TEXT,
                authenticated INTEGER NOT NULL
                    CHECK (authenticated IN (0, 1)),
                raw_payload TEXT,
                environment TEXT NOT NULL
                    CHECK (environment IN ('TEST', 'SANDBOX', 'PRODUCTION')),
                observed_at TEXT NOT NULL,
                created_at TEXT NOT NULL,

                UNIQUE(source, external_event_id)
            )
        """)

        # Contract-policy conclusion derived from external evidence.
        conn.execute("""
            CREATE TABLE contract_acceptance_decisions (
                decision_id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                acceptance_event_id TEXT NOT NULL,
                policy_name TEXT NOT NULL,
                policy_version TEXT NOT NULL,
                accepted INTEGER NOT NULL CHECK (accepted IN (0, 1)),
                rejection_reasons TEXT,
                environment TEXT NOT NULL
                    CHECK (environment IN ('TEST', 'SANDBOX', 'PRODUCTION')),
                created_at TEXT NOT NULL,

                FOREIGN KEY(acceptance_event_id)
                    REFERENCES external_acceptance_events(event_id),

                UNIQUE(
                    acceptance_event_id,
                    policy_name,
                    policy_version
                )
            )
        """)

        # Authoritative payment authorization evidence.
        conn.execute("""
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
            )
        """)

        # Immutable-ish normalized provider observations.
        conn.execute("""
            CREATE TABLE provider_events (
                event_id TEXT PRIMARY KEY,
                provider TEXT NOT NULL,
                provider_event_id TEXT NOT NULL,
                authorization_id TEXT,
                settlement_id TEXT,
                event_type TEXT NOT NULL,
                provider_status TEXT,
                authenticated INTEGER NOT NULL
                    CHECK (authenticated IN (0, 1)),
                raw_payload TEXT,
                environment TEXT NOT NULL
                    CHECK (environment IN ('TEST', 'SANDBOX', 'PRODUCTION')),
                observed_at TEXT NOT NULL,
                created_at TEXT NOT NULL,

                FOREIGN KEY(authorization_id)
                    REFERENCES payment_authorizations(authorization_id),

                UNIQUE(provider, provider_event_id)
            )
        """)

        # Canonical audit history for future settlement transitions.
        conn.execute("""
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
            )
        """)

        # Internal accounting fact, distinct from provider confirmation.
        conn.execute("""
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
            )
        """)

        now = datetime.now(timezone.utc).isoformat()

        conn.execute("""
            INSERT INTO schema_meta (
                version,
                migration_name,
                applied_at
            )
            VALUES (?, ?, ?)
        """, (
            MIGRATION_VERSION,
            MIGRATION_NAME,
            now
        ))

        conn.commit()

    print(f"[OK] Migration {MIGRATION_VERSION:03} applied.")


if __name__ == "__main__":
    apply_migration()
