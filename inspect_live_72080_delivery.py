import json
from pathlib import Path

path = Path("data/ledger.json")

with path.open("r", encoding="utf-8") as f:
    data = json.load(f)

records = data if isinstance(data, list) else data.get("dispatched_proposals", [])

matches = [
    row for row in records
    if isinstance(row, dict)
    and row.get("task_id") == "live_72080"
]

print("MATCH COUNT:", len(matches))

for row in matches:
    safe = {
        "task_id": row.get("task_id"),
        "status": row.get("status"),
        "dispatched_at": row.get("dispatched_at") or row.get("timestamp"),
        "pr_url": row.get("pr_url"),
        "bounty_usd": row.get("bounty_usd"),
    }
    print(safe)
