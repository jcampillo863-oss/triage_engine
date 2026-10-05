import json
from collections import defaultdict
from pathlib import Path

path = Path("data/ledger.json")

with path.open("r", encoding="utf-8") as f:
    data = json.load(f)

records = data if isinstance(data, list) else data.get("dispatched_proposals", [])

by_pr = defaultdict(list)

for row in records:
    if not isinstance(row, dict):
        continue

    pr_url = row.get("pr_url")

    if pr_url and pr_url != "N/A":
        by_pr[pr_url].append(row.get("task_id"))

duplicates = {
    pr: tasks
    for pr, tasks in by_pr.items()
    if len(tasks) > 1
}

print("TOTAL LEDGER RECORDS:", len(records))
print("RECORDS WITH PR:", len(by_pr))
print("DUPLICATE PR MAPPINGS:", len(duplicates))

for pr, tasks in duplicates.items():
    print(pr, "=>", tasks)
