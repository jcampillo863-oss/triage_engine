from paypal_adapter import (
    PayPalAdapterError,
    normalize_authorization,
)


passed = 0
failed = 0


def ok(name):
    global passed
    passed += 1
    print(f"[PASS] {name}")


def bad(name, detail):
    global failed
    failed += 1
    print(f"[FAIL] {name}: {detail}")


def payload(
    *,
    auth_id="AUTH-SYNTHETIC-001",
    status="CREATED",
    value="50.00",
    currency="AUD",
):
    return {
        "id": auth_id,
        "status": status,
        "amount": {
            "value": value,
            "currency_code": currency,
        },
    }


result = normalize_authorization(
    payload(),
    provider_event_id="PAYPAL-EVENT-001",
    authenticated=True,
)

if (
    result["provider"] == "paypal"
    and result["provider_authorization_id"]
        == "AUTH-SYNTHETIC-001"
    and result["amount_cents"] == 5000
    and result["currency"] == "AUD"
    and result["authenticated"] is True
):
    ok("Valid PayPal authorization normalized")
else:
    bad(
        "Valid PayPal authorization normalized",
        result,
    )


try:
    normalize_authorization(
        payload(),
        provider_event_id="PAYPAL-EVENT-002",
        authenticated=False,
    )

    bad(
        "Unauthenticated evidence rejected",
        "accepted unexpectedly",
    )
except PayPalAdapterError:
    ok("Unauthenticated evidence rejected")


try:
    normalize_authorization(
        payload(status="VOIDED"),
        provider_event_id="PAYPAL-EVENT-003",
        authenticated=True,
    )

    bad(
        "Ineligible authorization status rejected",
        "accepted unexpectedly",
    )
except PayPalAdapterError:
    ok("Ineligible authorization status rejected")


try:
    normalize_authorization(
        payload(value="0.00"),
        provider_event_id="PAYPAL-EVENT-004",
        authenticated=True,
    )

    bad(
        "Zero amount rejected",
        "accepted unexpectedly",
    )
except PayPalAdapterError:
    ok("Zero amount rejected")


try:
    normalize_authorization(
        payload(value="12.345"),
        provider_event_id="PAYPAL-EVENT-005",
        authenticated=True,
    )

    bad(
        "Unsupported monetary precision rejected",
        "accepted unexpectedly",
    )
except PayPalAdapterError:
    ok("Unsupported monetary precision rejected")


try:
    normalize_authorization(
        payload(currency=""),
        provider_event_id="PAYPAL-EVENT-006",
        authenticated=True,
    )

    bad(
        "Missing currency rejected",
        "accepted unexpectedly",
    )
except PayPalAdapterError:
    ok("Missing currency rejected")


try:
    normalize_authorization(
        payload(auth_id=""),
        provider_event_id="PAYPAL-EVENT-007",
        authenticated=True,
    )

    bad(
        "Missing authorization ID rejected",
        "accepted unexpectedly",
    )
except PayPalAdapterError:
    ok("Missing authorization ID rejected")


try:
    normalize_authorization(
        payload(),
        provider_event_id="",
        authenticated=True,
    )

    bad(
        "Missing provider event identity rejected",
        "accepted unexpectedly",
    )
except PayPalAdapterError:
    ok("Missing provider event identity rejected")


print()
print("TEST RESULTS")
print("PASS:", passed)
print("FAIL:", failed)

if failed:
    raise SystemExit(1)
