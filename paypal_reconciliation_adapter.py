from typing import Any, Dict
from urllib.parse import urlparse


class PayPalReconciliationError(RuntimeError):
    """Invalid or insufficient PayPal reconciliation evidence."""


def extract_parent_order_id(
    authorization: Dict[str, Any],
) -> str:
    """
    Extract the parent PayPal Order ID from an authorization's
    read-only 'up' relation.
    """

    if not isinstance(authorization, dict):
        raise PayPalReconciliationError(
            "Authorization evidence must be an object."
        )

    links = authorization.get("links")

    if not isinstance(links, list):
        raise PayPalReconciliationError(
            "Authorization evidence contains no links."
        )

    for link in links:
        if not isinstance(link, dict):
            continue

        if link.get("rel") != "up":
            continue

        href = link.get("href")

        if not isinstance(href, str) or not href.strip():
            continue

        parsed = urlparse(href)

        parts = [
            part
            for part in parsed.path.split("/")
            if part
        ]

        if (
            len(parts) >= 4
            and parts[-2] == "orders"
            and parts[-1]
        ):
            return parts[-1]

    raise PayPalReconciliationError(
        "Authorization evidence contains no valid parent Order."
    )


def normalize_reconciliation(
    authorization: Dict[str, Any],
    order: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Convert PayPal read-only evidence into the provider-neutral
    reconciliation vocabulary used by paypal_capture_service.

    Returns one of:

        {"outcome": "COMPLETED", "capture_id": "..."}
        {"outcome": "NOT_CAPTURED"}
        {"outcome": "UNRESOLVED"}
    """

    if not isinstance(authorization, dict):
        raise PayPalReconciliationError(
            "Authorization evidence must be an object."
        )

    if not isinstance(order, dict):
        raise PayPalReconciliationError(
            "Order evidence must be an object."
        )

    authorization_status = authorization.get("status")

    if not isinstance(authorization_status, str):
        return {"outcome": "UNRESOLVED"}

    authorization_status = authorization_status.upper()

    purchase_units = order.get("purchase_units")

    if not isinstance(purchase_units, list):
        return {"outcome": "UNRESOLVED"}

    captures = []

    for unit in purchase_units:
        if not isinstance(unit, dict):
            return {"outcome": "UNRESOLVED"}

        payments = unit.get("payments") or {}

        if not isinstance(payments, dict):
            return {"outcome": "UNRESOLVED"}

        unit_captures = payments.get("captures") or []

        if not isinstance(unit_captures, list):
            return {"outcome": "UNRESOLVED"}

        captures.extend(unit_captures)

    completed_captures = []

    for capture in captures:
        if not isinstance(capture, dict):
            return {"outcome": "UNRESOLVED"}

        capture_id = capture.get("id")
        capture_status = capture.get("status")

        if not isinstance(capture_status, str):
            return {"outcome": "UNRESOLVED"}

        if capture_status.upper() == "COMPLETED":
            if (
                not isinstance(capture_id, str)
                or not capture_id.strip()
            ):
                return {"outcome": "UNRESOLVED"}

            completed_captures.append(
                capture_id.strip()
            )

    # A single definitive completed capture is sufficient evidence.
    if len(completed_captures) == 1:
        return {
            "outcome": "COMPLETED",
            "capture_id": completed_captures[0],
        }

    # Multiple completed captures require human investigation.
    if len(completed_captures) > 1:
        return {"outcome": "UNRESOLVED"}

    # Our observed pre-capture PayPal state:
    # authorization CREATED + no captures.
    if (
        authorization_status == "CREATED"
        and len(captures) == 0
    ):
        return {"outcome": "NOT_CAPTURED"}

    return {"outcome": "UNRESOLVED"}