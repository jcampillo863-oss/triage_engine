from paypal_reconciliation_adapter import (
    extract_parent_order_id,
    normalize_reconciliation,
)


def make_authorization(status="CREATED"):
    return {
        "id": "FAKE-AUTH-001",
        "status": status,
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


def make_order(captures=None):
    if captures is None:
        captures = []

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
                    "captures": captures,
                }
            }
        ],
    }


def test_parent_order_extraction():
    authorization = make_authorization()

    order_id = extract_parent_order_id(
        authorization
    )

    assert order_id == "FAKE-ORDER-001"

    print("PARENT ORDER EXTRACTION: PASSED")


def test_completed_capture():
    authorization = make_authorization(
        status="CAPTURED"
    )

    order = make_order(
        captures=[
            {
                "id": "FAKE-CAPTURE-001",
                "status": "COMPLETED",
            }
        ]
    )

    result = normalize_reconciliation(
        authorization,
        order,
    )

    assert result == {
        "outcome": "COMPLETED",
        "capture_id": "FAKE-CAPTURE-001",
    }

    print("COMPLETED CAPTURE NORMALIZATION: PASSED")


def test_completed_order_without_capture():
    authorization = make_authorization(
        status="CREATED"
    )

    # Important:
    # The Order itself says COMPLETED, but there is
    # no completed payment capture.
    order = make_order(
        captures=[]
    )

    result = normalize_reconciliation(
        authorization,
        order,
    )

    assert result == {
        "outcome": "NOT_CAPTURED"
    }

    print("ORDER COMPLETED / NO CAPTURE: PASSED")


def test_multiple_completed_captures_unresolved():
    authorization = make_authorization(
        status="CAPTURED"
    )

    order = make_order(
        captures=[
            {
                "id": "FAKE-CAPTURE-001",
                "status": "COMPLETED",
            },
            {
                "id": "FAKE-CAPTURE-002",
                "status": "COMPLETED",
            },
        ]
    )

    result = normalize_reconciliation(
        authorization,
        order,
    )

    assert result == {
        "outcome": "UNRESOLVED"
    }

    print("MULTIPLE CAPTURES: PASSED")


def test_incomplete_evidence_unresolved():
    authorization = {
        "id": "FAKE-AUTH-001",
    }

    order = make_order()

    result = normalize_reconciliation(
        authorization,
        order,
    )

    assert result == {
        "outcome": "UNRESOLVED"
    }

    print("INCOMPLETE EVIDENCE: PASSED")


if __name__ == "__main__":
    test_parent_order_extraction()
    test_completed_capture()
    test_completed_order_without_capture()
    test_multiple_completed_captures_unresolved()
    test_incomplete_evidence_unresolved()

    print("PAYPAL RECONCILIATION ADAPTER: PASSED")