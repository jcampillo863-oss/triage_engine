from typing import Any, Callable, Dict

from paypal_reconciliation_adapter import (
    extract_parent_order_id,
    normalize_reconciliation,
)


class PayPalObservationError(RuntimeError):
    """PayPal evidence could not be observed safely."""


def observe_authorization(
    provider_authorization_id: str,
    *,
    get_authorization_fn: Callable[[str], Dict[str, Any]],
    get_order_fn: Callable[[str], Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Read PayPal state without causing financial side effects.

    This function performs observation only:
        authorization -> parent order -> normalization

    It does not capture, void, reauthorize, or modify payment state.
    """

    if (
        not isinstance(provider_authorization_id, str)
        or not provider_authorization_id.strip()
    ):
        raise PayPalObservationError(
            "Provider authorization ID is required."
        )

    authorization = get_authorization_fn(
        provider_authorization_id.strip()
    )

    if not isinstance(authorization, dict):
        raise PayPalObservationError(
            "PayPal authorization response is invalid."
        )

    observed_authorization_id = authorization.get("id")

    if observed_authorization_id != provider_authorization_id.strip():
        raise PayPalObservationError(
            "PayPal authorization identity mismatch."
        )

    order_id = extract_parent_order_id(
        authorization
    )

    order = get_order_fn(order_id)

    if not isinstance(order, dict):
        raise PayPalObservationError(
            "PayPal Order response is invalid."
        )

    observed_order_id = order.get("id")

    if observed_order_id != order_id:
        raise PayPalObservationError(
            "PayPal Order identity mismatch."
        )

    return normalize_reconciliation(
        authorization,
        order,
    )