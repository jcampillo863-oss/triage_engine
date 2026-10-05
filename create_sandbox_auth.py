import os
import requests
from dotenv import load_dotenv

load_dotenv(override=True)

# Uses PAYPAL_BASE_URL from .env or defaults to the same endpoint as settlement_worker.py
PAYPAL_BASE_URL = os.getenv("PAYPAL_BASE_URL", "https://api-m.paypal.com")

def create_authorization():
    client_id = os.getenv("PAYPAL_CLIENT_ID")
    client_secret = os.getenv("PAYPAL_CLIENT_SECRET")

    if not client_id or not client_secret:
        print("[ERROR] PAYPAL_CLIENT_ID or PAYPAL_CLIENT_SECRET missing in .env")
        return

    print(f"[INIT] Requesting OAuth token from {PAYPAL_BASE_URL}...")

    # 1. Get OAuth Access Token
    token_resp = requests.post(
        f"{PAYPAL_BASE_URL}/v1/oauth2/token",
        auth=(client_id, client_secret),
        headers={"Accept": "application/json"},
        data={"grant_type": "client_credentials"},
        timeout=10
    ).json()

    if "access_token" not in token_resp:
        print("[ERROR] Failed to obtain access token from PayPal:")
        print(token_resp)
        return

    token = token_resp["access_token"]
    print("[SUCCESS] OAuth Token retrieved.")

    # 2. Create Order with INTENT = AUTHORIZE
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    payload = {
        "intent": "AUTHORIZE",
        "purchase_units": [{
            "amount": {
                "currency_code": "AUD",
                "value": "100.00"
            },
            "description": "Bounty for heavy_task_99999"
        }]
    }

    response = requests.post(f"{PAYPAL_BASE_URL}/v2/checkout/orders", headers=headers, json=payload, timeout=10)
    print("\n[RESULT] PayPal Response:")
    print(response.json())

if __name__ == "__main__":
    create_authorization()