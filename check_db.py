import db

print("DB:", db.DB_PATH)

with db.get_db() as conn:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()

print("TABLES:")
for row in rows:
    print(" -", row[0])
