import db

canonical_tables = [
    "work_deliveries",
    "external_acceptance_events",
    "contract_acceptance_decisions",
    "payment_authorizations",
    "canonical_settlements",
    "canonical_settlement_journal",
    "provider_events",
    "revenue_ledger",
]

failed = False

with db.get_db() as conn:
    for table in canonical_tables:
        count = conn.execute(
            f"SELECT COUNT(*) AS n FROM {table}"
        ).fetchone()["n"]

        print(f"{table}: {count}")

        if count != 0:
            failed = True

    integrity = conn.execute(
        "PRAGMA integrity_check"
    ).fetchone()[0]

    fk_errors = conn.execute(
        "PRAGMA foreign_key_check"
    ).fetchall()

print("integrity_check:", integrity)
print("foreign_key_errors:", len(fk_errors))

if integrity != "ok":
    failed = True

if fk_errors:
    failed = True

if failed:
    print("POST-SUITE CHECK: FAILED")
    raise SystemExit(1)

print("POST-SUITE CHECK: PASSED")
