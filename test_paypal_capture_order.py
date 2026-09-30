import os
import requests
from requests.auth import HTTPBasicAuth

def capture_order():
    mode = os.getenv("PAYPAL_MODE", "sandbox")
    if mode == "live":
        base_url = "https://api-m.paypal.com"
    else:
        base_url = "https://api-m.sandbox.paypal.com"

    client_id = os.getenv("PAYPAL_CLIENT_ID")
    client_secret = os.getenv("PAYPAL_CLIENT_SECRET")
    
    # The Order ID we generated earlier
    order_id = "7L161944CL895091D"

    if not client_id or not client_secret:
        print("[ERROR] PAYPAL_CLIENT_ID or PAYPAL_CLIENT_SECRET environment variables are missing.")
        return

    # Step 1: Get Access Token
    auth_url = f"{base_url}/v1/oauth2/token"
    print(f"[INIT] Authenticating with PayPal ({mode} mode)...")
    auth_res = requests.post(
        auth_url,
        auth=HTTPBasicAuth(client_id, client_secret),
        data={"grant_type": "client_credentials"},
        timeout=10
    )

    if auth_res.status_code != 200:
        print(f"[FAILED] Authentication error: {auth_res.text}")
        return

    token = auth_res.json().get("access_token")

    # Step 2: Capture the Order
    capture_url = f"{base_url}/v2/checkout/orders/{order_id}/capture"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}"
    }

    print(f"[INIT] Attempting to capture payment for Order ID: {order_id}...")
    response = requests.post(capture_url, headers=headers, timeout=10)

    if response.status_code in (200, 201):
        capture_data = response.json()
        print("[SUCCESS] Order captured successfully!")
        print(f"Status: {capture_data.get('status')}")
        print(f"Capture ID: {capture_data.get('purchase_units', [{}])[0].get('payments', {}).get('captures', [{}])[0].get('id')}")
    else:
        print(f"[FAILED] Capture rejected. Status Code: {response.status_code}")
        print(f"Response: {response.text}")

if __name__ == "__main__":
    capture_order()