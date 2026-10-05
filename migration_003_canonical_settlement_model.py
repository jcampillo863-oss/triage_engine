from datetime import datetime, timezone
import db


MIGRATION_VERSION = 3
MIGRATION_NAME = "canonical_settlement_model"


def apply_migration():
    print(f"[MIGRATION {MIGRATION_VERSION:03}] Canonical Settlement Model")
    print(f"[DB] {db.DB_PATH}")

    with db.get_db() as conn:
        existing = conn.execute(
            "SELECT version FROM schema_meta WHERE version = ?",
            (MIGRATION_VERSION,)
        ).fetchone()

        if existing:
            print(f"[SKIP] Migration {MIGRATION_VERSION:03} already applied.")
            return

        conn.execute("""
            CREATE TABLE canonical_settlements (
                settlement_id TEXT PRIMARY KEY,

                task_id TEXT NOT NULL,
                obligation_id TEXT NOT NULL,

                contract_acceptance_decision_id TEXT NOT NULL,
                payment_authorization_id TEXT NOT NULL,

                amount_cents INTEGER NOT NULL
                    CHECK (amount_cents > 0),

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

                environment TEXT NOT NULL CHECK (
                    environment IN (
                        'TEST',
                        'SANDBOX',
                        'PRODUCTION'
                    )
                ),

                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,

                FOREIGN KEY(contract_acceptance_decision_id)
                    REFERENCES contract_acceptance_decisions(decision_id),

                FOREIGN KEY(payment_authorization_id)
                    REFERENCES payment_authorizations(authorization_id),

                UNIQUE(obligation_id),
                UNIQUE(payment_authorization_id)
            )
        """)

        conn.execute("""
            CREATE UNIQUE INDEX
            ux_canonical_provider_capture
            ON canonical_settlements (
                provider,
                provider_capture_id
            )
            WHERE provider_capture_id IS NOT NULL
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
