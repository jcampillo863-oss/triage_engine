import os
import requests
from requests.auth import HTTPBasicAuth
from dotenv import load_dotenv

# Load variables from your .env file
load_dotenv(override=True)

def test_live_auth():
    mode = os.getenv("PAYPAL_MODE", "sandbox")
    client_id = os.getenv("PAYPAL_CLIENT_ID")
    client_secret = os.getenv("PAYPAL_CLIENT_SECRET")

    # Determine correct base URL based on mode
    if mode == "live":
        base_url = "https://api-m.paypal.com"
    else:
        base_url = "https://api-m.sandbox.paypal.com"

    if not client_id or not client_secret:
        print("[ERROR] PAYPAL_CLIENT_ID or PAYPAL_CLIENT_SECRET is missing from your .env file.")
        return

    print(f"[INIT] Testing authentication for [{os.getenv('PAYPAL_NAME', 'AtlasAeon')}] in ({mode} mode)...")
    print(f"[INFO] Target Endpoint: {base_url}")

    # Request OAuth 2.0 Access Token
    auth_url = f"{base_url}/v1/oauth2/token"
    auth_res = requests.post(
        auth_url,
        auth=HTTPBasicAuth(client_id, client_secret),
        data={"grant_type": "client_credentials"},
        timeout=10
    )

    if auth_res.status_code == 200:
        token_data = auth_res.json()
        print("[SUCCESS] Live authentication handshake successful!")
        print(f"Token Type: {token_data.get('token_type')}")
        print(f"Expires In: {token_data.get('expires_in')} seconds")
        print(f"Access Token Prefix: {token_data.get('access_token')[:15]}...")
    else:
        print(f"[FAILED] Authentication error. Status Code: {auth_res.status_code}")
        print(f"Response: {auth_res.text}")

if __name__ == "__main__":
    test_live_auth()