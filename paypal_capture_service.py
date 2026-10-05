import os
from pathlib import Path
from dataclasses import dataclass
import hashlib
from typing import Any, Callable, Dict, Optional

import requests

from canonical_settlement_engine import (
    get_settlement,
    record_revenue,
    transition_settlement,
)


class CaptureServiceError(RuntimeError):
    """Canonical capture orchestration failure."""


class AmbiguousCaptureOutcome(CaptureServiceError):
    """
    Raised when we cannot determine whether the provider
    completed the capture.
    """


@dataclass(frozen=True)
class CaptureResult:
    settlement_id: str
    state: str
    provider_capture_id: Optional[str] = None


def _check_capture_gate(settlement):
    if (Path(__file__).resolve().parent / "data" / "STOP").exists():
        raise CaptureServiceError("Capture stopped by operator")
    if settlement["environment"] == "PRODUCTION" and os.getenv("CANONICAL_LIVE_CAPTURE_ENABLED") != "true":
        raise CaptureServiceError("Production capture is disabled")


def build_capture_request_id(settlement_id: str) -> str:
    """
    Produce a stable, fixed-length provider idempotency key
    for one canonical settlement.
    """

    if not isinstance(settlement_id, str) or not settlement_id.strip():
        raise CaptureServiceError("settlement_id is required.")

    digest = hashlib.sha256(
        settlement_id.strip().encode("utf-8")
    ).hexdigest()[:32]

    return f"cap-{digest}"

def _extract_provider_authorization_id(
    conn,
    settlement,
) -> str:
    authorization = conn.execute(
        """
        SELECT provider_authorization_id
        FROM payment_authorizations
        WHERE authorization_id = ?
        """,
        (settlement["payment_authorization_id"],),
    ).fetchone()

    if authorization is None:
        raise CaptureServiceError(
            "Canonical payment authorization does not exist."
        )

    provider_authorization_id = (
        authorization["provider_authorization_id"]
    )

    if (
        not isinstance(provider_authorization_id, str)
        or not provider_authorization_id.strip()
    ):
        raise CaptureServiceError(
            "Canonical payment authorization has no "
            "provider authorization ID."
        )

    return provider_authorization_id.strip()


def _extract_capture_identity(
    payload: Dict[str, Any],
) -> tuple[str, str]:
    if not isinstance(payload, dict):
        raise CaptureServiceError(
            "Provider capture response must be an object."
        )

    capture_id = payload.get("id")
    status = payload.get("status")

    if not isinstance(capture_id, str) or not capture_id.strip():
        raise CaptureServiceError(
            "Provider capture response has no capture ID."
        )

    if not isinstance(status, str) or not status.strip():
        raise CaptureServiceError(
            "Provider capture response has no status."
        )

    return capture_id.strip(), status.strip().upper()

def prepare_capture(
    conn,
    settlement_id: str,
) -> CaptureResult:
    """
    Durably prepare a canonical settlement for provider capture.

    This function does NOT contact PayPal.

    The caller owns the transaction and must commit the returned
    CAPTURE_REQUESTED state before any provider capture call occurs.
    """

    settlement = get_settlement(conn, settlement_id)

    if settlement is None:
        raise CaptureServiceError(
            "Canonical settlement does not exist."
        )

    _check_capture_gate(settlement)
    state = settlement["state"]

    _ensure_capture_attempt_schema(conn)

    # Exact replay of preparation is safe.
    if state == "CAPTURE_REQUESTED":
        return CaptureResult(
            settlement_id=settlement_id,
            state=state,
            provider_capture_id=settlement["provider_capture_id"],
        )

    if state != "PAYMENT_AUTHORIZED":
        raise CaptureServiceError(
            f"Settlement cannot be prepared for capture "
            f"from state {state}."
        )

    prepared = transition_settlement(
        conn,
        settlement_id,
        "CAPTURE_REQUESTED",
        reason=(
            "Canonical settlement durably prepared "
            "for provider capture."
        ),
        evidence_reference=build_capture_request_id(
            settlement_id
        ),
    )

    return CaptureResult(
        settlement_id=settlement_id,
        state=prepared["state"],
        provider_capture_id=prepared["provider_capture_id"],
    )

def _ensure_capture_attempt_schema(conn):
    existed = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='canonical_capture_attempts'").fetchone()
    if existed:
        return
    conn.execute("""CREATE TABLE IF NOT EXISTS canonical_capture_attempts (
        settlement_id TEXT PRIMARY KEY REFERENCES canonical_settlements(settlement_id),
        request_id TEXT NOT NULL UNIQUE,
        claimed_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
    )""")
    # Pre-upgrade prepared settlements may already have contacted the provider.
    # Quarantine them for observation instead of treating absent claims as permission.
    conn.execute("""INSERT OR IGNORE INTO canonical_capture_attempts(settlement_id,request_id)
        SELECT settlement_id, 'pre-upgrade-' || settlement_id
        FROM canonical_settlements WHERE state='CAPTURE_REQUESTED'""")


def _claim_capture_attempt(conn, settlement_id, request_id):
    # Auxiliary uniqueness guard; no provider authority or new economic state.
    # Preparation must already be committed before this function is called.
    _ensure_capture_attempt_schema(conn)
    conn.commit()
    conn.execute("BEGIN IMMEDIATE")
    try:
        fresh = get_settlement(conn, settlement_id)
        if fresh is None or fresh["state"] != "CAPTURE_REQUESTED":
            raise CaptureServiceError("Capture state changed; reconcile instead")
        existing = conn.execute(
            "SELECT 1 FROM canonical_capture_attempts WHERE settlement_id=?",
            (settlement_id,),
        ).fetchone()
        if existing:
            raise CaptureServiceError("Capture already claimed; reconcile instead of retry")
        conn.execute(
            "INSERT INTO canonical_capture_attempts(settlement_id,request_id) VALUES (?,?)",
            (settlement_id,request_id),
        )
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


def execute_capture(
    conn,
    settlement_id: str,
    *,
    capture_fn: Callable[..., Dict[str, Any]],
) -> CaptureResult:
    """
    Execute provider capture for an already-prepared settlement.

    PRECONDITION:
    CAPTURE_REQUESTED must already be durably committed.

    capture_fn is injected so tests can exercise provider behaviour
    without making network calls.
    """

    settlement = get_settlement(conn, settlement_id)

    if settlement is None:
        raise CaptureServiceError(
            "Canonical settlement does not exist."
        )

    if settlement["state"] != "CAPTURE_REQUESTED":
        raise CaptureServiceError(
            f"Provider capture cannot execute from state "
            f"{settlement['state']}."
        )

    # Critical durability guard:
    # refuse provider contact while the preparation transition
    # is still part of an uncommitted transaction.
    if conn.in_transaction:
        raise CaptureServiceError(
            "CAPTURE_REQUESTED must be committed before "
            "provider capture is attempted."
        )

    provider_authorization_id = (
        _extract_provider_authorization_id(
            conn,
            settlement,
        )
    )

    request_id = build_capture_request_id(
        settlement_id
    )

    _check_capture_gate(settlement)
    _claim_capture_attempt(conn, settlement_id, request_id)
    _check_capture_gate(settlement)
    try:
        payload = capture_fn(
            provider_authorization_id,
            request_id=request_id,
        )

        capture_id, status = _extract_capture_identity(payload)
        if status != "COMPLETED":
            raise CaptureServiceError("Provider capture is not definitively completed")
    except Exception:
        unknown = transition_settlement(
            conn, settlement_id, "OUTCOME_UNKNOWN",
            reason="Provider contact did not yield definitive completion evidence; reconciliation required.",
            evidence_reference=request_id,
        )
        return CaptureResult(settlement_id=settlement_id, state=unknown["state"], provider_capture_id=None)

    confirmed = transition_settlement(
        conn,
        settlement_id,
        "PROVIDER_CONFIRMED",
        reason=(
            "Provider returned definitive completed "
            "capture evidence."
        ),
        evidence_reference=request_id,
        provider_capture_id=capture_id,
    )

    return CaptureResult(
        settlement_id=settlement_id,
        state=confirmed["state"],
        provider_capture_id=capture_id,
    )

def reconcile_capture(
    conn,
    settlement_id: str,
    *,
    observe_fn: Callable[..., Dict[str, Any]],
) -> CaptureResult:
    """
    Reconcile an ambiguous provider capture outcome.

    observe_fn must be read-only and return provider-neutral
    reconciliation evidence.

    Expected observations:

        {"outcome": "COMPLETED", "capture_id": "..."}
        {"outcome": "NOT_CAPTURED"}
        {"outcome": "UNRESOLVED"}

    This function never retries the financial operation.
    """

    settlement = get_settlement(conn, settlement_id)

    if settlement is None:
        raise CaptureServiceError(
            f"Settlement not found: {settlement_id}"
        )

    current_state = settlement["state"]

    if current_state == "OUTCOME_UNKNOWN":
        transition_settlement(
            conn,
            settlement_id,
            "RECONCILING",
            reason="provider reconciliation started",
            evidence_reference="paypal_reconciliation",
        )

        # Deliberate crash boundary:
        # RECONCILING must be durable before external observation.
        conn.commit()

        settlement = get_settlement(
            conn,
            settlement_id,
        )

    elif current_state == "RECONCILING":
        # Restart/resume case.
        # The previous process may have committed RECONCILING
        # and died before or during provider observation.
        pass

    else:
        raise CaptureServiceError(
            "Reconciliation requires OUTCOME_UNKNOWN "
            "or RECONCILING state."
        )


    # RECONCILING must itself become durable before
    # external provider investigation occurs.
    conn.commit()

    provider_authorization_id = (
        _extract_provider_authorization_id(
            conn,
            settlement,
        )
    )

    observation = observe_fn(
        provider_authorization_id
    )

    if not isinstance(observation, dict):
        raise CaptureServiceError(
            "Reconciliation observation must be an object."
        )

    outcome = observation.get("outcome")

    if outcome == "COMPLETED":
        capture_id = observation.get("capture_id")

        if not isinstance(capture_id, str) or not capture_id.strip():
            raise CaptureServiceError(
                "Completed reconciliation evidence has "
                "no capture ID."
            )

        confirmed = transition_settlement(
            conn,
            settlement_id,
            "PROVIDER_CONFIRMED",
            reason=(
                "Read-only provider reconciliation confirmed "
                "that capture completed."
            ),
            evidence_reference="provider-reconciliation",
            provider_capture_id=capture_id.strip(),
        )

        return CaptureResult(
            settlement_id=settlement_id,
            state=confirmed["state"],
            provider_capture_id=capture_id.strip(),
        )

    if outcome == "NOT_CAPTURED":
        # No completed capture is currently visible.
        #
        # This is not sufficient evidence that the financial
        # operation definitively failed. Provider visibility may
        # lag after an ambiguous network outcome.
        #
        # Remain in the durable RECONCILING state. A later
        # read-only observation may resolve the outcome.
        return CaptureResult(
            settlement_id=settlement_id,
            state="RECONCILING",
            provider_capture_id=None,
        )

    unresolved = transition_settlement(
        conn,
        settlement_id,
        "MANUAL_REVIEW_REQUIRED",
        reason=(
            "Read-only provider reconciliation could not "
            "establish a definitive capture outcome."
        ),
        evidence_reference="provider-reconciliation",
    )

    return CaptureResult(
        settlement_id=settlement_id,
        state=unresolved["state"],
        provider_capture_id=None,
    )