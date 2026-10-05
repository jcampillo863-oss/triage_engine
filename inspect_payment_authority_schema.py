import db

tables = [
    "payment_authorizations",
    "contract_acceptance_decisions",
    "external_acceptance_events",
    "work_deliveries",
    "canonical_settlements",
]

with db.get_db() as conn:
    for table in tables:
        print()
        print("=" * 70)
        print(table)
        print("=" * 70)

        columns = conn.execute(
            f'PRAGMA table_info("{table}")'
        ).fetchall()

        for column in columns:
            print(
                column["name"],
                column["type"],
                f"NOTNULL={column['notnull']}",
                f"PK={column['pk']}",
            )

        print()
        print("FOREIGN KEYS:")

        foreign_keys = conn.execute(
            f'PRAGMA foreign_key_list("{table}")'
        ).fetchall()

        if not foreign_keys:
            print("(none)")
        else:
            for fk in foreign_keys:
                print(dict(fk))

        print()
        print("INDEXES:")

        indexes = conn.execute(
            f'PRAGMA index_list("{table}")'
        ).fetchall()

        if not indexes:
            print("(none)")
        else:
            for index in indexes:
                print(dict(index))

    print()
    print("=" * 70)
    print("DATABASE")
    print("=" * 70)

    print(
        "integrity_check:",
        conn.execute(
            "PRAGMA integrity_check"
        ).fetchone()[0],
    )

    fk_errors = conn.execute(
        "PRAGMA foreign_key_check"
    ).fetchall()

    print(
        "foreign_key_errors:",
        len(fk_errors),
    )
