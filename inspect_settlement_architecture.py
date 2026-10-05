import db

TABLES = [
    "settlement_records",
    "settlements",
    "settlement_journal",
]

with db.get_db() as conn:

    print("=== DATABASE ===")
    print(db.DB_PATH)

    for table in TABLES:
        print(f"\n{'=' * 70}")
        print(f"TABLE: {table}")
        print("=" * 70)

        print("\nCOLUMNS:")
        for row in conn.execute(f"PRAGMA table_info({table})"):
            print(dict(row))

        print("\nFOREIGN KEYS:")
        foreign_keys = conn.execute(
            f"PRAGMA foreign_key_list({table})"
        ).fetchall()

        if foreign_keys:
            for row in foreign_keys:
                print(dict(row))
        else:
            print("(none)")

        print("\nINDEXES:")
        indexes = conn.execute(
            f"PRAGMA index_list({table})"
        ).fetchall()

        if indexes:
            for row in indexes:
                print(dict(row))
        else:
            print("(none)")

        print("\nCREATE SQL:")
        row = conn.execute(
            """
            SELECT sql
            FROM sqlite_master
            WHERE type='table' AND name=?
            """,
            (table,),
        ).fetchone()

        print(row["sql"] if row else "(not found)")

        print("\nROWS:")
        rows = conn.execute(
            f"SELECT * FROM {table}"
        ).fetchall()

        print(f"COUNT: {len(rows)}")

        for row in rows:
            print(dict(row))

    print("\n" + "=" * 70)
    print("DATABASE CHECKS")
    print("=" * 70)

    print(
        "integrity_check:",
        conn.execute("PRAGMA integrity_check").fetchone()[0]
    )

    fk_errors = conn.execute(
        "PRAGMA foreign_key_check"
    ).fetchall()

    print("foreign_key_errors:", len(fk_errors))

    for row in fk_errors:
        print(tuple(row))
