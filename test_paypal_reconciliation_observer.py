from paypal_reconciliation_observer import (
    PayPalObservationError,
    observe_authorization,
)


def make_authorization():
    return {
        "id": "FAKE-AUTH-001",
        "status": "CREATED",
        "links": [
            {
                "rel": "up",
                "method": "GET",
                "href": (
                    "https://api-m.sandbox.paypal.com/"
                    "v2/checkout/orders/FAKE-ORDER-001"
                ),
            }
        ],
    }


def make_order():
    return {
        "id": "FAKE-ORDER-001",
        "status": "COMPLETED",
        "purchase_units": [
            {
                "payments": {
                    "authorizations": [
                        {
                            "id": "FAKE-AUTH-001",
                            "status": "CREATED",
                        }
                    ],
                    "captures": [],
                }
            }
        ],
    }


def test_read_only_observation():
    calls = {
        "authorization": 0,
        "order": 0,
        "capture": 0,
    }

    def fake_get_authorization(authorization_id):
        calls["authorization"] += 1
        assert authorization_id == "FAKE-AUTH-001"
        return make_authorization()

    def fake_get_order(order_id):
        calls["order"] += 1
        assert order_id == "FAKE-ORDER-001"
        return make_order()

    # This deliberately exists as a tripwire.
    # The observer is never given this function and therefore
    # has no path to perform a capture.
    def fake_capture(*args, **kwargs):
        calls["capture"] += 1
        raise AssertionError("Capture must never be called.")

    result = observe_authorization(
        "FAKE-AUTH-001",
        get_authorization_fn=fake_get_authorization,
        get_order_fn=fake_get_order,
    )

    assert result == {
        "outcome": "NOT_CAPTURED"
    }

    assert calls["authorization"] == 1
    assert calls["order"] == 1
    assert calls["capture"] == 0

    print("READ-ONLY OBSERVATION: PASSED")
    print("AUTHORIZATION READS:", calls["authorization"])
    print("ORDER READS:", calls["order"])
    print("CAPTURE CALLS:", calls["capture"])
    print("OUTCOME:", result["outcome"])


def test_identity_mismatch_rejected():
    def fake_get_authorization(_authorization_id):
        authorization = make_authorization()
        authorization["id"] = "WRONG-AUTH-ID"
        return authorization

    def fake_get_order(_order_id):
        return make_order()

    try:
        observe_authorization(
            "FAKE-AUTH-001",
            get_authorization_fn=fake_get_authorization,
            get_order_fn=fake_get_order,
        )
    except PayPalObservationError:
        print("AUTHORIZATION IDENTITY GUARD: PASSED")
    else:
        raise AssertionError(
            "Authorization identity mismatch was not rejected."
        )


def test_order_identity_mismatch_rejected():
    def fake_get_authorization(_authorization_id):
        return make_authorization()

    def fake_get_order(_order_id):
        order = make_order()
        order["id"] = "WRONG-ORDER-ID"
        return order

    try:
        observe_authorization(
            "FAKE-AUTH-001",
            get_authorization_fn=fake_get_authorization,
            get_order_fn=fake_get_order,
        )
    except PayPalObservationError:
        print("ORDER IDENTITY GUARD: PASSED")
    else:
        raise AssertionError(
            "Order identity mismatch was not rejected."
        )


if __name__ == "__main__":
    test_read_only_observation()
    test_identity_mismatch_rejected()
    test_order_identity_mismatch_rejected()

    print("PAYPAL READ-ONLY OBSERVER: PASSED")