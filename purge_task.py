import sqlite3, glob, json, os

print("=== CHECKING SQLITE DATABASES ===")
for db in glob.glob("**/*.db", recursive=True):
    try:
        conn = sqlite3.connect(db)
        cur = conn.cursor()
        tables = [t[0] for t in cur.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()]
        for t in tables:
            rows = cur.execute(f"SELECT * FROM {t}").fetchall()
            for r in rows:
                if '80304' in str(r):
                    print(f"Found 80304 in DB [{db}] Table [{t}]: {r}")
                    cur.execute(f"DELETE FROM {t} WHERE task_id LIKE '%80304%' OR id LIKE '%80304%';")
                    conn.commit()
                    print(f"-> Deleted from [{db}] [{t}]")
        conn.close()
    except Exception as e:
        print(f"Error checking {db}: {e}")

print("\n=== CHECKING ALL JSON FILES ===")
for jf in glob.glob("**/*.json", recursive=True):
    if "certificates" in jf:
        continue
    try:
        with open(jf, "r") as f:
            data = f.read()
        if "80304" in data:
            print(f"Found 80304 in JSON: {jf}")
    except Exception:
        pass
