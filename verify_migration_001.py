import db

with db.get_db() as conn:
    print("DB:", db.DB_PATH)

    print("foreign_keys:", conn.execute(
        "PRAGMA foreign_keys"
    ).fetchone()[0])

    print("journal_mode:", conn.execute(
        "PRAGMA journal_mode"
    ).fetchone()[0])

    print("busy_timeout:", conn.execute(
        "PRAGMA busy_timeout"
    ).fetchone()[0])

    print("\nMIGRATIONS:")
    for row in conn.execute(
        "SELECT version, migration_name, applied_at "
        "FROM schema_meta ORDER BY version"
    ):
        print(dict(row))

    print("\nTABLES:")
    for row in conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='table' ORDER BY name"
    ):
        print(" -", row[0])
