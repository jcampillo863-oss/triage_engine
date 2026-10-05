import hashlib
from datetime import datetime, timezone
from typing import Optional, Dict, Any
import db

# Allowed State Machine Transitions
VALID_TRANSITIONS = {
    "AUTHORIZED": ["SUBMITTING", "FAILED"],
    "SUBMITTING": ["SUBMITTED", "RECONCILE", "FAILED"],
    "SUBMITTED": ["PENDING", "SETTLED", "FAILED", "RECONCILE"],
    "PENDING": ["SETTLED", "FAILED", "RECONCILE"],
    "RECONCILE": ["SETTLED", "PENDING", "FAILED", "RECONCILE", "MANUAL_REVIEW_REQUIRED"],
    "MANUAL_REVIEW_REQUIRED": ["SETTLED", "FAILED"],
    "SETTLED": [], # Terminal state
    "FAILED": ["SUBMITTING"] # Retry path
}

def generate_settlement_id(task_id: str, pr_url: str) -> str:
    """Derives a deterministic settlement ID from the economic obligation."""
    raw_key = f"{task_id.strip().lower()}:{pr_url.strip().lower()}"
    digest = hashlib.sha256(raw_key.encode('utf-8')).hexdigest()[:16]
    return f"set_{digest}"

def get_or_create_settlement(
    task_id: str,
    pr_url: str,
    amount: str = "5.00",
    currency: str = "AUD",
    recipient_email: str = "jcampillo863-facilitator@gmail.com"
) -> Dict[str, Any]:
    """Retrieves an existing settlement or creates a new AUTHORIZED entry idempotently."""
    settlement_id = generate_settlement_id(task_id, pr_url)
    now = datetime.now(timezone.utc).isoformat()

    with db.get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM settlements WHERE settlement_id = ?", (settlement_id,))
        row = cursor.fetchone()

        if row:
            return dict(row)

        # Insert initial AUTHORIZED record
        cursor.execute("""
            INSERT INTO settlements (settlement_id, task_id, pr_url, amount, currency, recipient_email, state, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 'AUTHORIZED', ?, ?)
        """, (settlement_id, task_id, pr_url, amount, currency, recipient_email, now, now))

        cursor.execute("""
            INSERT INTO settlement_journal (settlement_id, from_state, to_state, reason, timestamp)
            VALUES (?, NULL, 'AUTHORIZED', 'Settlement initialized from GitHub merge event', ?)
        """, (settlement_id, now))

        conn.commit()

        cursor.execute("SELECT * FROM settlements WHERE settlement_id = ?", (settlement_id,))
        return dict(cursor.fetchone())

def transition_state(
    settlement_id: str,
    to_state: str,
    reason: str,
    provider_payload: Optional[str] = None
) -> bool:
    """Transitions settlement state strictly following state machine rules."""
    with db.get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT state FROM settlements WHERE settlement_id = ?", (settlement_id,))
        row = cursor.fetchone()

        if not row:
            raise ValueError(f"Settlement {settlement_id} not found.")

        current_state = row["state"]

        # Verify valid state transition
        if to_state not in VALID_TRANSITIONS.get(current_state, []):
            print(f"[WARN] Invalid transition attempted: {current_state} -> {to_state} for {settlement_id}")
            return False

        db.record_transition(settlement_id, current_state, to_state, reason, provider_payload)
        return True