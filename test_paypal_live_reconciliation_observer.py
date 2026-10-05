import sqlite3

from db import DB_PATH
from paypal_sandbox_client import (
    get_authorization,
    get_order,
)
from paypal_reconciliation_observer import (
    observe_authorization,
)


def get_existing_provider_authorization_id():
    """
    Locate the provider authorization belonging to the existing
    canonical Sandbox settlement.

    No financial state is modified.
    """

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    try:
        row = conn.execute(
            """
            SELECT
                cs.settlement_id,
                cs.state,
                pa.provider_authorization_id
            FROM canonical_settlements AS cs
            JOIN payment_authorizations AS pa
              ON pa.authorization_id = cs.payment_authorization_id
            WHERE pa.environment = 'SANDBOX'
            ORDER BY cs.rowid DESC
            LIMIT 1
            """
        ).fetchone()

        if row is None:
            raise RuntimeError(
                "No canonical Sandbox settlement was found."
            )

        provider_authorization_id = (
            row["provider_authorization_id"]
        )

        if not provider_authorization_id:
            raise RuntimeError(
                "Settlement has no provider authorization ID."
            )

        return (
            row["state"],
            provider_authorization_id,
        )

    finally:
        conn.close()


def main():
    settlement_state, provider_authorization_id = (
        get_existing_provider_authorization_id()
    )

    counters = {
        "authorization_reads": 0,
        "order_reads": 0,
    }

    observed = {
        "authorization_status": None,
        "order_status": None,
        "capture_count": None,
    }

    def safe_get_authorization(authorization_id):
        counters["authorization_reads"] += 1

        payload = get_authorization(
            authorization_id
        )

        observed["authorization_status"] = (
            payload.get("status")
        )

        return payload

    def safe_get_order(order_id):
        counters["order_reads"] += 1

        payload = get_order(order_id)

        observed["order_status"] = payload.get(
            "status"
        )

        capture_count = 0

        for unit in payload.get(
            "purchase_units", []
        ):
            payments = unit.get(
                "payments", {}
            )

            captures = payments.get(
                "captures", []
            )

            if isinstance(captures, list):
                capture_count += len(captures)

        observed["capture_count"] = (
            capture_count
        )

        return payload

    result = observe_authorization(
        provider_authorization_id,
        get_authorization_fn=safe_get_authorization,
        get_order_fn=safe_get_order,
    )

    print(
        "CANONICAL SETTLEMENT STATE:",
        settlement_state,
    )

    print(
        "AUTHORIZATION READS:",
        counters["authorization_reads"],
    )

    print(
        "AUTHORIZATION STATUS:",
        observed["authorization_status"],
    )

    print(
        "ORDER READS:",
        counters["order_reads"],
    )

    print(
        "ORDER STATUS:",
        observed["order_status"],
    )

    print(
        "CAPTURE COUNT:",
        observed["capture_count"],
    )

    print(
        "OBSERVATION:",
        result["outcome"],
    )

    print(
        "CAPTURE ATTEMPTED:",
        False,
    )


if __name__ == "__main__":
    main()