from datetime import datetime, timezone

import db


MIGRATION_VERSION = 4
MIGRATION_NAME = "work_delivery_provenance"


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
                    f"Migration version {MIGRATION_VERSION} already "
                    f"belongs to {existing['migration_name']!r}."
                )

            print(
                f"Migration {MIGRATION_VERSION} already applied: "
                f"{MIGRATION_NAME}"
            )
            return

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS work_deliveries (
                delivery_id TEXT PRIMARY KEY,

                task_id TEXT NOT NULL,

                repository TEXT NOT NULL,
                pull_request_number INTEGER NOT NULL
                    CHECK (pull_request_number > 0),

                pull_request_url TEXT NOT NULL,

                head_branch TEXT,
                base_branch TEXT,

                dispatched_commit_hash TEXT,

                status TEXT NOT NULL
                    CHECK (
                        status IN (
                            'DISPATCHED',
                            'SUPERSEDED',
                            'CANCELLED'
                        )
                    ),

                environment TEXT NOT NULL
                    CHECK (
                        environment IN (
                            'TEST',
                            'SANDBOX',
                            'PRODUCTION'
                        )
                    ),

                dispatched_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,

                UNIQUE (
                    repository,
                    pull_request_number,
                    environment
                ),

                UNIQUE (
                    pull_request_url,
                    environment
                )
            )
            """
        )

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS
                ix_work_deliveries_task
            ON work_deliveries (
                task_id,
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
