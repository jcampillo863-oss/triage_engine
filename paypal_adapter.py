from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


class PayPalAdapterError(ValueError):
    pass


SUPPORTED_AUTHORIZATION_STATUSES = {
    "CREATED",
}


def _money_to_cents(value):
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise PayPalAdapterError(
            "Invalid PayPal monetary value."
        )

    if amount <= 0:
        raise PayPalAdapterError(
            "PayPal amount must be positive."
        )

    quantized = amount.quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP,
    )

    if amount != quantized:
        raise PayPalAdapterError(
            "PayPal amount has unsupported precision."
        )

    return int(quantized * 100)


def normalize_authorization(
    payload,
    *,
    provider_event_id,
    authenticated,
):
    if not isinstance(payload, dict):
        raise PayPalAdapterError(
            "PayPal authorization payload must be a dictionary."
        )

    if authenticated is not True:
        raise PayPalAdapterError(
            "PayPal authorization evidence is not authenticated."
        )

    provider_event_id = (
        provider_event_id or ""
    ).strip()

    if not provider_event_id:
        raise PayPalAdapterError(
            "Provider event ID is required."
        )

    authorization_id = (
        payload.get("id") or ""
    ).strip()

    if not authorization_id:
        raise PayPalAdapterError(
            "PayPal authorization ID is required."
        )

    status = (
        payload.get("status") or ""
    ).strip().upper()

    if not status:
        raise PayPalAdapterError(
            "PayPal authorization status is required."
        )

    if status not in SUPPORTED_AUTHORIZATION_STATUSES:
        raise PayPalAdapterError(
            f"PayPal authorization status is not eligible: {status}"
        )

    amount = payload.get("amount")

    if not isinstance(amount, dict):
        raise PayPalAdapterError(
            "PayPal authorization amount is required."
        )

    currency = (
        amount.get("currency_code") or ""
    ).strip().upper()

    if (
        len(currency) != 3
        or not currency.isalpha()
    ):
        raise PayPalAdapterError(
            "Invalid PayPal currency code."
        )

    amount_cents = _money_to_cents(
        amount.get("value")
    )

    return {
        "provider": "paypal",
        "provider_event_id": provider_event_id,
        "provider_authorization_id": authorization_id,
        "event_type": "authorization.established",
        "provider_status": status,
        "amount_cents": amount_cents,
        "currency": currency,
        "authenticated": True,
        "raw_payload": payload,
    }
