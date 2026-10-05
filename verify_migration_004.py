import db

with db.get_db() as conn:
    migration = conn.execute(
        """
        SELECT version, migration_name, applied_at
        FROM schema_meta
        WHERE version = 4
        """
    ).fetchone()

    print("MIGRATION:", dict(migration) if migration else None)

    columns = conn.execute(
        'PRAGMA table_info("work_deliveries")'
    ).fetchall()

    print("COLUMNS:")
    for column in columns:
        print(
            column["name"],
            column["type"],
            "NOTNULL=" + str(column["notnull"]),
        )

    count = conn.execute(
        "SELECT COUNT(*) AS n FROM work_deliveries"
    ).fetchone()["n"]

    print("WORK DELIVERY ROWS:", count)

    print(
        "integrity_check:",
        conn.execute(
            "PRAGMA integrity_check"
        ).fetchone()[0],
    )

    fk_errors = conn.execute(
        "PRAGMA foreign_key_check"
    ).fetchall()

    print("foreign_key_errors:", len(fk_errors))
