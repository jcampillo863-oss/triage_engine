import os
import requests
from requests.auth import HTTPBasicAuth

def create_mock_order():
    mode = os.getenv("PAYPAL_MODE", "sandbox")
    if mode == "live":
        base_url = "https://api-m.paypal.com"
    else:
        base_url = "https://api-m.sandbox.paypal.com"

    client_id = os.getenv("PAYPAL_CLIENT_ID")
    client_secret = os.getenv("PAYPAL_CLIENT_SECRET")

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

    # Step 2: Create a Mock Order ($10.00 USD)
    order_url = f"{base_url}/v2/checkout/orders"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}"
    }
    payload = {
        "intent": "CAPTURE",
        "purchase_units": [
            {
                "amount": {
                    "currency_code": "USD",
                    "value": "10.00"
                },
                "description": "AtlasAeon Pipeline Test Order"
            }
        ]
    }

    print("[INIT] Dispatching mock order request to PayPal API...")
    response = requests.post(order_url, headers=headers, json=payload, timeout=10)

    if response.status_code in (200, 201):
        order_data = response.json()
        print("[SUCCESS] Mock order created successfully!")
        print(f"Order ID: {order_data.get('id')}")
        print(f"Status: {order_data.get('status')}")

        # Extract and print the checkout approval URL
        for link in order_data.get("links", []):
            if link.get("rel") == "approve":
                print(f"\n[INFO] Sandbox Checkout Approval Link:\n{link.get('href')}\n")
                print("You can paste this link into a browser to simulate approving the test payment!")
    else:
        print(f"[FAILED] Order creation rejected. Status Code: {response.status_code}")
        print(f"Response: {response.text}")

if __name__ == "__main__":
    create_mock_order()