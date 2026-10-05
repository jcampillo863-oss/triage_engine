from datetime import datetime, timezone

import db


MIGRATION_VERSION = 6
MIGRATION_NAME = "payment_authorization_evidence"


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def migrate():
    with db.get_db() as conn:
        existing = conn.execute(
            """
            SELECT migration_name
            FROM schema_meta
            WHERE version = ?
            """,
            (MIGRATION_VERSION,),
        ).fetchone()

        if existing:
            if existing["migration_name"] != MIGRATION_NAME:
                raise RuntimeError(
                    f"Migration version {MIGRATION_VERSION} "
                    f"already belongs to "
                    f"{existing['migration_name']!r}."
                )

            print(
                f"Migration {MIGRATION_VERSION} "
                f"already applied: {MIGRATION_NAME}"
            )
            return

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS
                payment_authorization_events (
                    event_id TEXT PRIMARY KEY,

                    provider TEXT NOT NULL,
                    provider_event_id TEXT NOT NULL,
                    provider_authorization_id TEXT NOT NULL,

                    event_type TEXT NOT NULL,
                    provider_status TEXT NOT NULL,

                    amount_cents INTEGER NOT NULL
                        CHECK (amount_cents > 0),

                    currency TEXT NOT NULL,

                    authenticated INTEGER NOT NULL
                        CHECK (authenticated IN (0, 1)),

                    raw_payload TEXT,

                    environment TEXT NOT NULL
                        CHECK (
                            environment IN (
                                'TEST',
                                'SANDBOX',
                                'PRODUCTION'
                            )
                        ),

                    observed_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,

                    UNIQUE (
                        provider,
                        provider_event_id,
                        environment
                    )
                )
            """
        )

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS
                ix_payment_auth_events_authorization
            ON payment_authorization_events (
                provider,
                provider_authorization_id,
                environment
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS
                payment_authorization_verifications (
                    verification_id TEXT PRIMARY KEY,

                    authorization_event_id TEXT NOT NULL
                        REFERENCES payment_authorization_events(
                            event_id
                        ),

                    task_id TEXT NOT NULL,

                    contract_acceptance_decision_id TEXT NOT NULL
                        REFERENCES contract_acceptance_decisions(
                            decision_id
                        ),

                    verification_policy TEXT NOT NULL,
                    policy_version TEXT NOT NULL,

                    verified INTEGER NOT NULL
                        CHECK (verified IN (0, 1)),

                    rejection_reasons TEXT,

                    environment TEXT NOT NULL
                        CHECK (
                            environment IN (
                                'TEST',
                                'SANDBOX',
                                'PRODUCTION'
                            )
                        ),

                    created_at TEXT NOT NULL,

                    UNIQUE (
                        authorization_event_id,
                        task_id,
                        contract_acceptance_decision_id,
                        verification_policy,
                        policy_version
                    )
                )
            """
        )

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS
                ix_payment_auth_verification_contract
            ON payment_authorization_verifications (
                contract_acceptance_decision_id,
                environment
            )
            """
        )

        conn.execute(
            """
            INSERT INTO schema_meta (
                version,
                migration_name,
                applied_at
            )
            VALUES (?, ?, ?)
            """,
            (
                MIGRATION_VERSION,
                MIGRATION_NAME,
                utc_now(),
            ),
        )

        conn.commit()

        print(
            f"Migration {MIGRATION_VERSION} applied: "
            f"{MIGRATION_NAME}"
        )


if __name__ == "__main__":
    migrate()
