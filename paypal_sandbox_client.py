import os
import uuid
from typing import Any, Dict

import requests


SANDBOX_BASE_URL = "https://api-m.sandbox.paypal.com"
TIMEOUT_SECONDS = 15


class PayPalSandboxError(RuntimeError):
    pass


def _credentials():
    client_id = os.getenv("PAYPAL_SANDBOX_CLIENT_ID")
    client_secret = os.getenv("PAYPAL_SANDBOX_CLIENT_SECRET")

    if not client_id or not client_secret:
        raise PayPalSandboxError(
            "Missing PAYPAL_SANDBOX_CLIENT_ID or "
            "PAYPAL_SANDBOX_CLIENT_SECRET."
        )

    return client_id, client_secret


def get_access_token() -> str:
    client_id, client_secret = _credentials()

    response = requests.post(
        f"{SANDBOX_BASE_URL}/v1/oauth2/token",
        auth=(client_id, client_secret),
        data={"grant_type": "client_credentials"},
        headers={
            "Accept": "application/json",
            "Accept-Language": "en_US",
        },
        timeout=TIMEOUT_SECONDS,
    )

    if response.status_code != 200:
        raise PayPalSandboxError(
            f"Sandbox OAuth failed: HTTP {response.status_code} "
        )

    payload = response.json()
    token = payload.get("access_token")

    if not token:
        raise PayPalSandboxError(
            "Sandbox OAuth response contained no access token."
        )

    return token


def _money_value(amount_cents: int) -> str:
    if isinstance(amount_cents, bool) or not isinstance(amount_cents, int):
        raise PayPalSandboxError("amount_cents must be an integer.")

    if amount_cents <= 0:
        raise PayPalSandboxError("amount_cents must be positive.")

    return f"{amount_cents / 100:.2f}"


def _currency_code(currency: str) -> str:
    if not isinstance(currency, str):
        raise PayPalSandboxError("currency must be a string.")

    code = currency.strip().upper()

    if len(code) != 3 or not code.isalpha():
        raise PayPalSandboxError(
            "currency must be a 3-letter currency code."
        )

    return code


def create_authorize_order(
    amount_cents: int,
    currency: str = "AUD",
    reference_id: str = "marketplace-sandbox",
) -> Dict[str, Any]:
    """
    Create a PayPal SANDBOX order using AUTHORIZE intent.

    The buyer approves the order now, but funds are not captured here.
    Capture is a separate later operation.
    """

    token = get_access_token()
    value = _money_value(amount_cents)
    currency_code = _currency_code(currency)

    if not isinstance(reference_id, str) or not reference_id.strip():
        raise PayPalSandboxError("reference_id is required.")

    request_id = f"sandbox-order-{uuid.uuid4()}"

    payload = {
        "intent": "AUTHORIZE",

        "payment_source": {
            "paypal": {
                "experience_context": {
                    "user_action": "PAY_NOW",
                    "shipping_preference": "NO_SHIPPING",
                    "return_url": "http://127.0.0.1:8091/paypal/return",
                    "cancel_url": "http://127.0.0.1:8091/paypal/cancel",
                }
            }
        },

        "purchase_units": [
            {
                "reference_id": reference_id.strip(),
                "amount": {
                    "currency_code": currency_code,
                    "value": value,
                },
            }
        ],
    }

    response = requests.post(
        f"{SANDBOX_BASE_URL}/v2/checkout/orders",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "PayPal-Request-Id": request_id,
            "Prefer": "return=representation",
        },
        json=payload,
        timeout=TIMEOUT_SECONDS,
    )

    if response.status_code not in (200, 201):
        raise PayPalSandboxError(
            f"Create sandbox order failed: "
            f"HTTP {response.status_code} {response.text}"
        )

    data = response.json()

    order_id = data.get("id")
    status = data.get("status")

    if not order_id:
        raise PayPalSandboxError(
            "PayPal created an order but returned no order ID."
        )

    approve_url = None

    for link in data.get("links", []):
        if link.get("rel") in ("payer-action", "approve"):
            approve_url = link.get("href")
            break

    if not approve_url:
        raise PayPalSandboxError(
            "PayPal order response contained no buyer approval URL."
        )

    return {
        "order_id": order_id,
        "status": status,
        "approve_url": approve_url,
        "request_id": request_id,
    }


def authorize_approved_order(order_id: str) -> Dict[str, Any]:
    """
    Convert a buyer-approved SANDBOX order into a PayPal authorization.

    This does NOT capture the funds.
    """

    if not isinstance(order_id, str) or not order_id.strip():
        raise PayPalSandboxError("order_id is required.")

    order_id = order_id.strip()
    token = get_access_token()

    request_id = f"sandbox-authorize-{order_id}"

    response = requests.post(
        f"{SANDBOX_BASE_URL}/v2/checkout/orders/{order_id}/authorize",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "PayPal-Request-Id": request_id,
            "Prefer": "return=representation",
        },
        json={},
        timeout=TIMEOUT_SECONDS,
    )

    if response.status_code not in (200, 201):
        raise PayPalSandboxError(
            f"Authorize sandbox order failed: "
            f"HTTP {response.status_code} {response.text}"
        )

    data = response.json()

    authorizations = []

    for purchase_unit in data.get("purchase_units", []):
        payments = purchase_unit.get("payments", {})
        authorizations.extend(payments.get("authorizations", []))

    if len(authorizations) != 1:
        raise PayPalSandboxError(
            "Expected exactly one PayPal authorization; "
            f"received {len(authorizations)}."
        )

    return {
        "order_id": data.get("id"),
        "order_status": data.get("status"),
        "authorization": authorizations[0],
        "request_id": request_id,
    }

def get_authorization(authorization_id: str) -> Dict[str, Any]:
    """
    Retrieve an existing PayPal SANDBOX authorization.

    Read-only operation. Does not capture, void, or modify the authorization.
    """

    if not isinstance(authorization_id, str) or not authorization_id.strip():
        raise PayPalSandboxError("authorization_id is required.")

    authorization_id = authorization_id.strip()
    token = get_access_token()

    response = requests.get(
        f"{SANDBOX_BASE_URL}/v2/payments/authorizations/{authorization_id}",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        },
        timeout=TIMEOUT_SECONDS,
    )

    if response.status_code != 200:
        raise PayPalSandboxError(
            f"Get sandbox authorization failed: "
            f"HTTP {response.status_code} {response.text}"
        )

    data = response.json()

    if data.get("id") != authorization_id:
        raise PayPalSandboxError(
            "PayPal returned an unexpected authorization ID."
        )

    return data

def get_order(order_id: str) -> Dict[str, Any]:
    """
    Retrieve an existing PayPal SANDBOX order.

    Read-only operation.
    """

    if not isinstance(order_id, str) or not order_id.strip():
        raise PayPalSandboxError("order_id is required.")

    order_id = order_id.strip()
    token = get_access_token()

    response = requests.get(
        f"{SANDBOX_BASE_URL}/v2/checkout/orders/{order_id}",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        },
        timeout=TIMEOUT_SECONDS,
    )

    if response.status_code != 200:
        raise PayPalSandboxError(
            f"Get sandbox order failed: "
            f"HTTP {response.status_code} {response.text}"
        )

    data = response.json()

    if data.get("id") != order_id:
        raise PayPalSandboxError(
            "PayPal returned an unexpected order ID."
        )

    return data

def capture_authorization(
    authorization_id: str,
    *,
    request_id: str,
) -> Dict[str, Any]:
    """
    Capture an existing PayPal SANDBOX authorization.

    MODIFYING OPERATION.

    The caller must supply a stable PayPal-Request-Id so retries
    remain idempotent. Canonical settlement state is deliberately
    outside this client.
    """

    if not isinstance(authorization_id, str) or not authorization_id.strip():
        raise PayPalSandboxError("authorization_id is required.")

    if not isinstance(request_id, str) or not request_id.strip():
        raise PayPalSandboxError("request_id is required.")

    authorization_id = authorization_id.strip()
    request_id = request_id.strip()

    token = get_access_token()

    response = requests.post(
        (
            f"{SANDBOX_BASE_URL}/v2/payments/authorizations/"
            f"{authorization_id}/capture"
        ),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "PayPal-Request-Id": request_id,
            "Prefer": "return=representation",
        },
        json={},
        timeout=TIMEOUT_SECONDS,
    )

    if response.status_code not in (200, 201):
        raise PayPalSandboxError(
            f"Capture sandbox authorization failed: "
            f"HTTP {response.status_code} {response.text}"
        )

    data = response.json()

    capture_id = data.get("id")
    status = data.get("status")

    if not isinstance(capture_id, str) or not capture_id.strip():
        raise PayPalSandboxError(
            "PayPal capture response contained no capture ID."
        )

    if not isinstance(status, str) or not status.strip():
        raise PayPalSandboxError(
            "PayPal capture response contained no status."
        )

    return data