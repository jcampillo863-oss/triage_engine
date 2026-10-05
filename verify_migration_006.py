import db


with db.get_db() as conn:
    migration = conn.execute(
        """
        SELECT version, migration_name, applied_at
        FROM schema_meta
        WHERE version = 6
        """
    ).fetchone()

    print(
        "MIGRATION:",
        dict(migration) if migration else None,
    )

    tables = [
        "payment_authorization_events",
        "payment_authorization_verifications",
    ]

    for table in tables:
        print()
        print(table)

        columns = conn.execute(
            f'PRAGMA table_info("{table}")'
        ).fetchall()

        for column in columns:
            print(
                column["name"],
                column["type"],
                f"NOTNULL={column['notnull']}",
            )

        count = conn.execute(
            f"SELECT COUNT(*) AS n FROM {table}"
        ).fetchone()["n"]

        print("ROWS:", count)

    existing_auths = conn.execute(
        """
        SELECT COUNT(*) AS n
        FROM payment_authorizations
        """
    ).fetchone()["n"]

    print()
    print(
        "PAYMENT AUTHORIZATIONS:",
        existing_auths,
    )

    integrity = conn.execute(
        "PRAGMA integrity_check"
    ).fetchone()[0]

    fk_errors = conn.execute(
        "PRAGMA foreign_key_check"
    ).fetchall()

    print("integrity_check:", integrity)
    print(
        "foreign_key_errors:",
        len(fk_errors),
    )
