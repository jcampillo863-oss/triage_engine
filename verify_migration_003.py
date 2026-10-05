import db

with db.get_db() as conn:

    print("=== MIGRATIONS ===")
    for row in conn.execute(
        "SELECT version, migration_name, applied_at "
        "FROM schema_meta ORDER BY version"
    ):
        print(dict(row))

    print("\n=== CANONICAL SETTLEMENT SCHEMA ===")
    for row in conn.execute(
        "PRAGMA table_info(canonical_settlements)"
    ):
        print(dict(row))

    print("\n=== FOREIGN KEYS ===")
    for row in conn.execute(
        "PRAGMA foreign_key_list(canonical_settlements)"
    ):
        print(dict(row))

    print("\n=== INDEXES ===")
    for row in conn.execute(
        "PRAGMA index_list(canonical_settlements)"
    ):
        print(dict(row))

    print("\n=== ROW COUNTS ===")

    tables = [
        "settlements",
        "settlement_records",
        "canonical_settlements",
        "canonical_settlement_journal",
        "payment_authorizations",
        "contract_acceptance_decisions",
        "revenue_ledger",
    ]

    for table in tables:
        count = conn.execute(
            f"SELECT COUNT(*) FROM {table}"
        ).fetchone()[0]

        print(f"{table}: {count}")

    print("\n=== DATABASE CHECKS ===")

    print(
        "integrity_check:",
        conn.execute(
            "PRAGMA integrity_check"
        ).fetchone()[0]
    )

    errors = conn.execute(
        "PRAGMA foreign_key_check"
    ).fetchall()

    print("foreign_key_errors:", len(errors))

    for row in errors:
        print(tuple(row))
