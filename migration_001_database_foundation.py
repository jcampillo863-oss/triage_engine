import sqlite3
from datetime import datetime, timezone

import db


MIGRATION_VERSION = 1
MIGRATION_NAME = "database_foundation"


def apply_migration():
    print(f"[MIGRATION {MIGRATION_VERSION:03}] Database Foundation")
    print(f"[DB] {db.DB_PATH}")

    with db.get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS schema_meta (
                version INTEGER PRIMARY KEY,
                migration_name TEXT NOT NULL,
                applied_at TEXT NOT NULL
            )
        """)

        existing = conn.execute(
            "SELECT version FROM schema_meta WHERE version = ?",
            (MIGRATION_VERSION,)
        ).fetchone()

        if existing:
            print(
                f"[SKIP] Migration {MIGRATION_VERSION:03} "
                "has already been applied."
            )
            return

        now = datetime.now(timezone.utc).isoformat()

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
                now,
            ),
        )

        conn.commit()

    print(f"[OK] Migration {MIGRATION_VERSION:03} applied.")


if __name__ == "__main__":
    apply_migration()
