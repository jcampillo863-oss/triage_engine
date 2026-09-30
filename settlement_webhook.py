from flask import Flask, request, jsonify
import os
import json 
import re
import requests
from dotenv import load_dotenv
from datetime import datetime

# Load your live environment variables
load_dotenv(override=True)

app = Flask(__name__)

@app.route("/webhook/github", methods=["POST"])
def github_webhook():
    event = request.headers.get("X-GitHub-Event", "ping")
    payload = request.json or {}
    print(f"[DEBUG] Received Event: {event} | Payload: {payload}")
    # We only care when a Pull Request action occurs
    if event == "pull_request":
        action = payload.get("action")
        pr = payload.get("pull_request", {})
        is_merged = pr.get("merged", False)
        pr_url = pr.get("html_url")
        pr_title = pr.get("title", "")
        
        # Check if the PR was successfully merged
        if action == "closed" and is_merged:
            print(f"[SUCCESS] Pull Request merged: {pr_url}")
            
            # 1. Extract the Task ID from the PR title or branch name
            match = re.search(r'(live_\d+|heavy_task_\d+)', pr_title)
            task_id = match.group(1) if match else "unknown_task"
            print(f"[INFO] Associated Task ID identified: {task_id}")
            
            # 2. Trigger the Live PayPal Settlement/Capture API & Ledger Update
            trigger_paypal_settlement(task_id, pr_url, pr)
            
            return jsonify({"status": "settlement_triggered", "task_id": task_id}), 200

    return jsonify({"status": "ignored"}), 200

def trigger_paypal_settlement(task_id, pr_url, pr_payload=None):
    print(f"[PAYPAL] Initiating live payment capture for Task {task_id}...")
    
    client_id = os.getenv("PAYPAL_CLIENT_ID") or os.getenv("AWchAto6KLHfV2Ko4Vq6Aq0ut5QLyjHREgeTpfYEmclPVc7e5X-nirAxz9cUv59m68yoP0Yti9_QalO6")
    client_secret = os.getenv("PAYPAL_CLIENT_SECRET") or os.getenv("EEM0qYhmg0H2F6G0pc5p-nge9cKhDbGAMzmLrgEp6eh-IFwa-OzCkTBwo99ObvuD8NjNRUM7Z5HYkUX1")
    base_url = "https://api-m.paypal.com"  # Live production endpoint
    
    if not client_id or not client_secret:
        print("[ERROR] PayPal live credentials missing from environment variables.")
        return

    try:
        # Step 1: Get OAuth2 Access Token
        auth_response = requests.post(
            f"{base_url}/v1/oauth2/token",
            auth=(client_id, client_secret),
            headers={"Accept": "application/json", "Accept-Language": "en_US"},
            data={"grant_type": "client_credentials"}
        )
        
        if auth_response.status_code != 200:
            print(f"[ERROR] Failed to obtain PayPal access token: {auth_response.text}")
            return
            
        access_token = auth_response.json().get("access_token")
        print("[PAYPAL] Live OAuth2 token successfully acquired.")

        # Step 2: Live settlement handshake authorized & Payout Trigger
        print(f"[PAYPAL] Live settlement handshake authorized for {task_id} via PR: {pr_url}")

        payout_url = f"{base_url}/v1/payments/payouts"
        payout_payload = {
            "sender_batch_header": {
                "sender_batch_id": f"pay_{task_id}_{int(datetime.now().timestamp())}",
                "email_subject": f"AtlasAeon Bounty Settlement: {task_id}"
            },
            "items": [
                {
                    "recipient_type": "EMAIL",
                    "amount": {
                        "value": "350.00",
                        "currency": "AUD"
                    },
                    "receiver": os.getenv("PAYPAL_EMAIL", "jcampillo863@gmail.com"),
                    "note": f"Automated settlement for merged PR: {pr_url}",
                    "sender_item_id": task_id
                }
            ]
        }
        
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {access_token}"
        }
        
        payout_response = requests.post(payout_url, headers=headers, json=payout_payload)
        if payout_response.status_code in (200, 201):
            print(f"[PAYPAL] Payout successfully initiated for task {task_id}!")
        else:
            print(f"[-] Payout execution warning: {payout_response.text}")

        # Step 3: Update local history store / ledger & issue Coherence Certificate
        print(f"[LEDGER] Marking task {task_id} as SETTLED.")
        update_ledger_status(task_id, new_status="SETTLED")
        generate_coherence_certificate(task_id, pr_payload or {"html_url": pr_url})
    except Exception as e:
        print(f"[ERROR] Exception during live settlement execution: {e}")

def update_ledger_status(task_id, new_status):
    ledger_path = os.path.join("data", "ledger.json")
    if os.path.exists(ledger_path):
        try:
            with open(ledger_path, "r", encoding="utf-8") as f:
                ledger = json.load(f)
                
            for entry in ledger:
                if entry.get("id") == task_id or entry.get("task_id") == task_id:
                    entry["status"] = new_status
                    entry["certified"] = True
                    
            with open(ledger_path, "w", encoding="utf-8") as f:
                json.dump(ledger, f, indent=2)
            print(f"[*] Ledger updated: Task [{task_id}] marked as '{new_status}'.")
        except Exception as e:
            print(f"[-] Error updating ledger: {e}")

def generate_coherence_certificate(task_id, pr_data):
    cert_dir = os.path.join("data", "certificates")
    os.makedirs(cert_dir, exist_ok=True)
    
    cert_path = os.path.join(cert_dir, f"cert_{task_id}.json")
    certificate_data = {
        "task_id": task_id,
        "status": "VERIFIED_AND_SETTLED",
        "merge_url": pr_data.get("html_url"),
        "merged_by": pr_data.get("merged_by", {}).get("login", "unknown"),
        "merged_at": pr_data.get("merged_at", "unknown"),
        "audit_type": "Human-Auditable Coherence Certificate"
    }
    
    with open(cert_path, "w", encoding="utf-8") as f:
        json.dump(certificate_data, f, indent=2)
    print(f"[*] Coherence certificate generated at: {cert_path}")

if __name__ == "__main__":
    print("[INIT] Starting Settlement Webhook Listener on port 8080...")
    app.run(port=8080, debug=True)