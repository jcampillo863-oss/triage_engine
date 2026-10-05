import os
import pytest
import requests
from requests.auth import HTTPBasicAuth

def test_paypal_handshake():
    # Toggle between sandbox and live as needed
    mode = os.getenv("PAYPAL_MODE", "sandbox")
    if mode == "live":
        base_url = "https://api-m.paypal.com"
    else:
        base_url = "https://api-m.sandbox.paypal.com"

    client_id = os.getenv("PAYPAL_CLIENT_ID")
    client_secret = os.getenv("PAYPAL_CLIENT_SECRET")

    if not client_id or not client_secret:
        pytest.fail(
            "[ERROR] PAYPAL_CLIENT_ID or PAYPAL_CLIENT_SECRET environment variables are missing.\n"
            "Set them in your terminal via: $env:PAYPAL_CLIENT_ID='your_id'"
        )

    auth_url = f"{base_url}/v1/oauth2/token"
    headers = {
        "Accept": "application/json",
        "Accept-Language": "en_US",
    }
    data = {"grant_type": "client_credentials"}

    print(f"\n[INIT] Attempting OAuth handshake with PayPal ({mode} mode)...")
    try:
        response = requests.post(
            auth_url,
            auth=HTTPBasicAuth(client_id, client_secret),
            headers=headers,
            data=data,
            timeout=10
        )

        assert response.status_code == 200, (
            f"[FAILED] Handshake rejected. Status Code: {response.status_code}\n"
            f"Response: {response.text}"
        )

        token_data = response.json()
        print("[SUCCESS] API Handshake established successfully!")
        print(f"Token Type: {token_data.get('token_type')}")
        print(f"Expires In: {token_data.get('expires_in')} seconds")

    except requests.RequestException as e:
        pytest.fail(f"[ERROR] Connection failed due to network or timeout error: {e}")

if __name__ == "__main__":
    test_paypal_handshake()