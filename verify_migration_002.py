import db

with db.get_db() as conn:
    print("=== MIGRATIONS ===")
    for row in conn.execute(
        "SELECT version, migration_name, applied_at "
        "FROM schema_meta ORDER BY version"
    ):
        print(dict(row))

    print("\n=== INTEGRITY ===")
    print("integrity_check:",
          conn.execute("PRAGMA integrity_check").fetchone()[0])

    fk_errors = conn.execute("PRAGMA foreign_key_check").fetchall()
    print("foreign_key_errors:", len(fk_errors))
    for row in fk_errors:
        print(tuple(row))

    print("\n=== TABLES ===")
    for row in conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='table' ORDER BY name"
    ):
        print(" -", row[0])

    print("\n=== CANONICAL ROW COUNTS ===")
    tables = [
        "technical_decisions",
        "external_acceptance_events",
        "contract_acceptance_decisions",
        "payment_authorizations",
        "provider_events",
        "canonical_settlement_journal",
        "revenue_ledger",
    ]

    for table in tables:
        count = conn.execute(
            f"SELECT COUNT(*) FROM {table}"
        ).fetchone()[0]
        print(f"{table}: {count}")
