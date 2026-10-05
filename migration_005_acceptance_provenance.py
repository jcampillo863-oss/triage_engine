from datetime import datetime, timezone

import db


MIGRATION_VERSION = 5
MIGRATION_NAME = "acceptance_provenance_linkage"


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def column_exists(conn, table, column):
    rows = conn.execute(
        f'PRAGMA table_info("{table}")'
    ).fetchall()

    return any(
        row["name"] == column
        for row in rows
    )


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

        if not column_exists(
            conn,
            "external_acceptance_events",
            "work_delivery_id",
        ):
            conn.execute(
                """
                ALTER TABLE external_acceptance_events
                ADD COLUMN work_delivery_id TEXT
                    REFERENCES work_deliveries(delivery_id)
                """
            )

        if not column_exists(
            conn,
            "external_acceptance_events",
            "pull_request_number",
        ):
            conn.execute(
                """
                ALTER TABLE external_acceptance_events
                ADD COLUMN pull_request_number INTEGER
                    CHECK (
                        pull_request_number IS NULL
                        OR pull_request_number > 0
                    )
                """
            )

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS
                ix_external_acceptance_delivery
            ON external_acceptance_events (
                work_delivery_id
            )
            """
        )

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS
                ix_external_acceptance_repo_pr
            ON external_acceptance_events (
                repository,
                pull_request_number,
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
