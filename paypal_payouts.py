raise RuntimeError("Retired legacy module; use canonical authorities")
import os
import requests
from dotenv import load_dotenv

load_dotenv()

PAYPAL_CLIENT_ID = os.getenv("PAYPAL_CLIENT_ID")
PAYPAL_CLIENT_SECRET = os.getenv("PAYPAL_CLIENT_SECRET")
PAYPAL_RECEIVER_EMAIL = os.getenv("PAYPAL_RECEIVER_EMAIL", "jcampillo863@gmail.com")
PAYPAL_ENV = os.getenv("PAYPAL_ENV", "live")

BASE_URL = "https://api-m.paypal.com" if PAYPAL_ENV.lower() == "live" else "https://api-m.sandbox.paypal.com"

def get_paypal_access_token():
    url = f"{BASE_URL}/v1/oauth2/token"
    res = requests.post(
        url,
        auth=(PAYPAL_CLIENT_ID, PAYPAL_CLIENT_SECRET),
        data={"grant_type": "client_credentials"},
        headers={"Accept": "application/json", "Accept-Language": "en_US"}
    )
    res.raise_for_status()
    return res.json()["access_token"]

def execute_payout(task_id: str, amount_usd: float, recipient_email: str = None):
    if not recipient_email:
        recipient_email = PAYPAL_RECEIVER_EMAIL

    token = get_paypal_access_token()
    url = f"{BASE_URL}/v1/payments/payouts"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}"
    }
    payload = {
        "sender_batch_header": {
            "sender_batch_id": f"batch_{task_id}",
            "email_subject": f"AtlasAeon Settlement: {task_id}"
        },
        "items": [
            {
                "recipient_type": "EMAIL",
                "amount": {"value": f"{amount_usd:.2f}", "currency": "USD"},
                "receiver": recipient_email,
                "note": f"Automated settlement payout for task {task_id}",
                "sender_item_id": task_id
            }
        ]
    }
    res = requests.post(url, json=payload, headers=headers)
    res.raise_for_status()
    return res.json()

if __name__ == "__main__":
    print(f"PayPal Payout Module initialized for environment: {PAYPAL_ENV}")
