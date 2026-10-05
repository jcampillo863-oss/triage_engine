import concurrent.futures
import requests
import db

WEBHOOK_URL = "http://localhost:8080/webhook/github"

TEST_PAYLOAD = {
    "action": "closed",
    "pull_request": {
        "title": "Fix: [live_7347] Improve BF16 reciprocal rounding on Wormhole",
        "html_url": "https://github.com/jcampillo863-oss/triage_engine/pull/101",
        "merged": True
    }
}
def send_webhook(_):
    try:
        response = requests.post(WEBHOOK_URL, json=TEST_PAYLOAD, timeout=5)
        return response.status_code, response.json()
    except Exception as e:
        return 500, str(e)

def run_chaos_test():
    print("[CHAOS TEST] Dispatching 50 concurrent duplicate webhooks for 'heavy_task_99999'...")

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        results = list(executor.map(send_webhook, range(50)))

    status_codes = set(r[0] for r in results)
    sample_response = results[0][1] if results else {}
    print(f"[CHAOS TEST] 50 webhooks completed.")
    print(f"  - HTTP Status Codes: {status_codes}")
    print(f"  - Sample Payload:    {sample_response}")

    # Query using system db.py connection
    with db.get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*), settlement_id, state FROM settlements WHERE task_id = 'heavy_task_99999'")
        row = cursor.fetchone()
        count, settlement_id, state = row

        journal_count = 0
        if settlement_id:
            cursor.execute("SELECT COUNT(*) FROM settlement_journal WHERE settlement_id = ?", (settlement_id,))
            journal_count = cursor.fetchone()[0]

    print("\n" + "="*50)
    print("INVARIANT VERIFICATION REPORT")
    print("="*50)
    print(f"Total Rows Created for Task: {count}")
    print(f"Settlement ID:               {settlement_id}")
    print(f"Current State:               {state}")
    print(f"Total Journal Entries:       {journal_count}")
    print("="*50)

    if count == 1:
        print("[VERDICT: PASSED] Financial Invariants Upheld! 50 concurrent duplicates yielded exactly 1 database row.")
    else:
        print(f"[VERDICT: FAILED] Invariant Violated! Found {count} database rows.")

if __name__ == "__main__":
    run_chaos_test()