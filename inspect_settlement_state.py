import db

with db.get_db() as conn:
    conn.row_factory = __import__("sqlite3").Row

    for table in ["settlements", "settlement_records", "acceptance_decisions"]:
        print(f"\n=== {table} ===")
        try:
            rows = conn.execute(f"SELECT * FROM {table}").fetchall()
            print("ROW COUNT:", len(rows))

            for row in rows:
                d = dict(row)

                # Don't print potentially sensitive/provider payload fields.
                safe = {
                    k: v for k, v in d.items()
                    if k not in {
                        "recipient_email",
                        "provider_payload",
                        "raw_payload"
                    }
                }
                print(safe)

        except Exception as e:
            print("ERROR:", e)
