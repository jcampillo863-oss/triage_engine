raise RuntimeError("Retired legacy module; use canonical authorities")
import os
import json
import time
import requests
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv
import db
import settlement_engine as se

load_dotenv(override=True)

# 1. Bind environment variables dynamically
PAYPAL_MODE = os.getenv("PAYPAL_MODE", "sandbox").lower()
MOCK_MODE = PAYPAL_MODE == "mock"  # Only mock if explicitly set to 'mock'

PAYPAL_BASE_URL = (
    "https://api-m.paypal.com" if PAYPAL_MODE == "live" else "https://api-m.sandbox.paypal.com"
)

def get_paypal_token() -> str:
    client_id = os.getenv("PAYPAL_CLIENT_ID")
    client_secret = os.getenv("PAYPAL_CLIENT_SECRET")
    
    if not client_id or not client_secret:
        raise ValueError("[CRITICAL] PayPal credentials missing from environment.")

    resp = requests.post(
        f"{PAYPAL_BASE_URL}/v1/oauth2/token",
        auth=(client_id, client_secret),
        headers={"Accept": "application/json", "Accept-Language": "en_US"},
        data={"grant_type": "client_credentials"},
        timeout=10
    )
    resp.raise_for_status()
    return resp.json()["access_token"]

def generate_coherence_certificate(settlement_id: str):
    """Generates a human-readable audit certificate upon authoritative settlement."""
    with db.get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM settlements WHERE settlement_id = ?", (settlement_id,))
        settlement = dict(cursor.fetchone())
        
        cursor.execute("SELECT * FROM settlement_journal WHERE settlement_id = ? ORDER BY id ASC", (settlement_id,))
        journal = [dict(r) for r in cursor.fetchall()]

    cert = {
        "certificate_id": f"cert_{settlement['task_id']}",
        "settlement_id": settlement_id,
        "task_id": settlement["task_id"],
        "pr_url": settlement["pr_url"],
        "financial_summary": {
            "type": "INBOUND_CAPTURE",
            "amount": settlement["amount"],
            "currency": settlement["currency"],
            "capture_id": settlement["provider_batch_id"]
        },
        "audit_state": settlement["state"],
        "transition_journal": journal,
        "generated_at": datetime.now(timezone.utc).isoformat()
    }
    
    cert_dir = Path("data/certificates")
    cert_dir.mkdir(parents=True, exist_ok=True)
    cert_path = cert_dir / f"cert_{settlement['task_id']}.json"
    
    with open(cert_path, "w") as f:
        json.dump(cert, f, indent=2)
        
    print(f"[CERTIFICATE] Coherence Certificate issued at {cert_path}")

def execute_outbound_payout(settlement: dict) -> dict:
    """Disburses settled funds directly to the developer's PayPal account via PayPal Batch Payouts API."""
    recipient_email = os.getenv("PAYPAL_RECEIVER_EMAIL", "jcampillo863@gmail.com")
    settlement_id = settlement["settlement_id"]
    task_id = settlement["task_id"]
    amount = float(settlement["amount"])
    currency = settlement.get("currency", "USD")

    if MOCK_MODE:
        print(f"[MOCK PAYPAL] Simulating outbound payout of {currency} {amount:.2f} to {recipient_email}...")
        return {"batch_header": {"payout_batch_id": f"PAYOUT_MOCK_{settlement_id}", "batch_status": "SUCCESS"}}

    token = get_paypal_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    payload = {
        "sender_batch_header": {
            "sender_batch_id": f"payout_{settlement_id}_{int(time.time())}",
            "email_subject": f"AtlasAeon Settlement Payout: Task {task_id}"
        },
        "items": [
            {
                "recipient_type": "EMAIL",
                "amount": {"value": f"{amount:.2f}", "currency": currency},
                "receiver": recipient_email,
                "note": f"Automated payout for completed task {task_id}",
                "sender_item_id": task_id
            }
        ]
    }

    resp = requests.post(
        f"{PAYPAL_BASE_URL}/v1/payments/payouts",
        headers=headers,
        json=payload,
        timeout=15
    )
    resp.raise_for_status()
    return resp.json()

def capture_authorized_payment(settlement: dict) -> bool:
    """Captures funds from an authorized client payment upon evidence verification."""
    settlement_id = settlement["settlement_id"]
    
    if not se.transition_state(settlement_id, "SUBMITTING", "Worker initiating PayPal payment capture"):
        return False

    auth_id = settlement.get("task_id") or settlement.get("provider_batch_id")

    try:
        if MOCK_MODE:
            print(f"[MOCK PAYPAL] Simulating successful 201 capture for '{auth_id}'...")
            resp_status_code = 201
            response_data = {
                "id": f"CAP_MOCK_{auth_id}",
                "status": "COMPLETED",
                "amount": {
                    "value": settlement["amount"],
                    "currency": settlement["currency"]
                }
            }
        else:
            token = get_paypal_token()
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "PayPal-Request-Id": settlement_id
            }
            payload = {
                "amount": {
                    "value": str(settlement["amount"]),
                    "currency": settlement["currency"]
                },
                "final_capture": True,
                "note_to_payer": f"Settlement capture for completed task {settlement['task_id']}"
            }
            resp = requests.post(
                f"{PAYPAL_BASE_URL}/v2/payments/authorizations/{auth_id}/capture",
                headers=headers,
                json=payload,
                timeout=15
            )
            resp_status_code = resp.status_code
            response_data = resp.json()
        
            if resp_status_code in [200, 201]:
                        capture_id = response_data.get("id")
                        status = response_data.get("status")

                        with db.get_db() as conn:
                            conn.cursor().execute(
                                "UPDATE settlements SET provider_batch_id = ? WHERE settlement_id = ?",
                                (capture_id, settlement_id)
                            )
                            conn.commit()

                        if status == "COMPLETED":
                            se.transition_state(settlement_id, "SUBMITTED", "PayPal payment capture accepted", json.dumps(response_data))
                            se.transition_state(settlement_id, "SETTLED", "PayPal verified payment capture COMPLETED", json.dumps(response_data))

                            # --- OUTBOUND PAYOUT TRIGGER ---
                            try:
                                payout_data = execute_outbound_payout(settlement)
                                payout_batch_id = payout_data.get("batch_header", {}).get("payout_batch_id")
                                print(f"[PAYPAL OUTBOUND] Payout initiated for {settlement_id}. Batch ID: {payout_batch_id}")
                                se.transition_state(settlement_id, "DISBURSED", f"Payout issued: {payout_batch_id}", json.dumps(payout_data))
                            except Exception as pe:
                                print(f"[PAYPAL OUTBOUND ERROR] Payout failed for {settlement_id}: {pe}")
                                se.transition_state(settlement_id, "DISBURSEMENT_FAILED", f"Payout execution error: {str(pe)}")

                            generate_coherence_certificate(settlement_id)
                        else:
                            se.transition_state(settlement_id, "SUBMITTED", f"Capture accepted with status {status}", json.dumps(response_data))
                            return True
            else:
                se.transition_state(settlement_id, "FAILED", f"PayPal capture rejected: {resp_status_code}", json.dumps(response_data))
                return False

    except Exception as e:
        print(f"[ERROR] Network/API exception during payment capture: {e}")
        se.transition_state(settlement_id, "RECONCILE", f"Ambiguous capture failure: {str(e)}")
        return False

def reconcile_settlement(settlement: dict):
    """Queries PayPal to verify the capture status of pending/submitted transactions."""
    settlement_id = settlement["settlement_id"]
    capture_id = settlement["provider_batch_id"]
    
    if not capture_id:
        return

    try:
        token = get_paypal_token()
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        
        resp = requests.get(f"{PAYPAL_BASE_URL}/v2/payments/captures/{capture_id}", headers=headers, timeout=10)
        
        if resp.status_code == 200:
            data = resp.json()
            status = data.get("status")
            
            if status == "COMPLETED":
                se.transition_state(settlement_id, "SETTLED", "Reconciled: Payment capture verified COMPLETED", json.dumps(data))
                generate_coherence_certificate(settlement_id)
            elif status == "PENDING":
                se.transition_state(settlement_id, "PENDING", "Reconciled: Payment capture PENDING", json.dumps(data))
            else:
                se.transition_state(settlement_id, "FAILED", f"Reconciled: Payment capture in state {status}", json.dumps(data))
    except Exception as e:
        print(f"[WARN] Reconciliation query failed for {settlement_id}: {e}")

def run_worker_cycle():
    """Single pass of the background worker loop."""
    with db.get_db() as conn:
        cursor = conn.cursor()
        
        # 1. Process AUTHORIZED captures
        cursor.execute("SELECT * FROM settlements WHERE state = 'AUTHORIZED'")
        for row in cursor.fetchall():
            s = dict(row)
            print(f"[WORKER] Executing inbound capture for settlement {s['settlement_id']}...")
            capture_authorized_payment(s)
            
        # 2. Reconcile pending/submitted captures
        cursor.execute("SELECT * FROM settlements WHERE state IN ('SUBMITTED', 'PENDING', 'RECONCILE')")
        for row in cursor.fetchall():
            s = dict(row)
            print(f"[WORKER] Reconciling capture for settlement {s['settlement_id']}...")
            reconcile_settlement(s)

if __name__ == "__main__":
    print(f"[SETTLEMENT WORKER] Starting execution loop in {PAYPAL_MODE.upper()} mode (Polling every 10s)...")
    while True:
        try:
            run_worker_cycle()
        except Exception as e:
            print(f"[WORKER ERROR] Unhandled exception in worker loop: {e}")
        time.sleep(10)