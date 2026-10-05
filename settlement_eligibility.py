"""Canonical eligibility authority. No payment, provider or settlement operations."""
import hashlib
import json
from datetime import datetime, timezone
import sqlite3

POLICY_NAME = "canonical_settlement_eligibility"
POLICY_VERSION = "1"

class SettlementEligibilityError(ValueError):
    pass

# The same predicate guards application issuance and direct SQL insertion.
# Raw observations remain separate from this approval artifact.
CHAIN_QUERY = """
SELECT d.producer_id, v.validator_id, d.dispatched_commit_hash
FROM work_deliveries d
JOIN raw_evidence e ON e.evidence_id = :evidence_id
JOIN validation_results v ON v.result_id = :validation_result_id
JOIN technical_decisions t ON t.decision_id = :technical_decision_id
JOIN contract_acceptance_decisions c ON c.decision_id = :contract_acceptance_decision_id
JOIN external_acceptance_events x ON x.event_id = c.acceptance_event_id
WHERE d.delivery_id = :work_delivery_id
  AND d.task_id = :task_id AND e.task_id = :task_id
  AND c.task_id = :task_id AND x.task_id = :task_id
  AND d.environment = :environment AND t.environment = :environment
  AND c.environment = :environment AND x.environment = :environment
  AND d.status = 'DISPATCHED'
  AND typeof(d.producer_id) = 'text' AND length(trim(d.producer_id)) > 0
  AND typeof(v.validator_id) = 'text' AND length(trim(v.validator_id)) > 0
  AND d.producer_id = trim(d.producer_id) AND v.validator_id = trim(v.validator_id)
  AND d.producer_id <> v.validator_id
  AND typeof(d.dispatched_commit_hash) = 'text' AND length(trim(d.dispatched_commit_hash)) > 0
  AND d.dispatched_commit_hash = trim(d.dispatched_commit_hash)
  AND d.dispatched_commit_hash = e.git_commit_hash
  AND v.evidence_id = e.evidence_id AND v.status = 'PASSED'
  AND t.evidence_id = e.evidence_id AND t.validation_result_id = v.result_id
  AND t.accepted = 1 AND c.accepted = 1 AND x.authenticated = 1
  AND x.work_delivery_id = d.delivery_id
  AND x.repository = d.repository AND x.pull_request_number = d.pull_request_number
  AND x.pull_request_url = d.pull_request_url
"""


def approve_settlement_eligibility(conn, *, task_id, work_delivery_id, evidence_id,
        validation_result_id, technical_decision_id, contract_acceptance_decision_id, environment):
    """Issue one immutable approval from an exact coherent chain; caller commits."""
    fields = dict(task_id=task_id, work_delivery_id=work_delivery_id, evidence_id=evidence_id,
        validation_result_id=validation_result_id, technical_decision_id=technical_decision_id,
        contract_acceptance_decision_id=contract_acceptance_decision_id, environment=environment)
    if environment not in {"TEST", "SANDBOX", "PRODUCTION"}:
        raise SettlementEligibilityError("Invalid eligibility environment")
    for name, value in fields.items():
        if name == "validation_result_id":
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise SettlementEligibilityError("Invalid validation result identity")
        elif not isinstance(value, str) or not value or value != value.strip():
            raise SettlementEligibilityError("Missing or invalid eligibility identity")
    try:
        chain = conn.execute(CHAIN_QUERY, fields).fetchone()
    except sqlite3.OperationalError as exc:
        raise SettlementEligibilityError("Eligibility schema is unavailable; apply migration 008") from exc
    if chain is None:
        raise SettlementEligibilityError("Eligibility chain is incomplete, inconsistent or not approved")
    fields.update(producer_id=chain[0], validator_id=chain[1], dispatched_commit_hash=chain[2],
        policy_name=POLICY_NAME, policy_version=POLICY_VERSION, approved=1)
    identity = json.dumps(fields, sort_keys=True, separators=(",", ":"))
    decision_id = "elig_" + hashlib.sha256(identity.encode()).hexdigest()
    stored = conn.execute("SELECT * FROM settlement_eligibility_decisions WHERE decision_id=?", (decision_id,)).fetchone()
    if stored:
        if any(stored[key] != value for key, value in fields.items()):
            raise SettlementEligibilityError("Eligibility replay conflicts with immutable approval")
        return dict(stored)
    fields.update(decision_id=decision_id, created_at=datetime.now(timezone.utc).isoformat())
    columns = list(fields)
    try:
        conn.execute("INSERT INTO settlement_eligibility_decisions (" + ",".join(columns) +
            ") VALUES (" + ",".join(":" + key for key in columns) + ")", fields)
    except sqlite3.IntegrityError:
        # A simultaneous exact issuer may have committed this immutable approval.
        existing = conn.execute("SELECT * FROM settlement_eligibility_decisions WHERE decision_id=?", (decision_id,)).fetchone()
        if existing is None or any(existing[key] != value for key, value in fields.items() if key != "created_at"):
            raise

    return dict(conn.execute("SELECT * FROM settlement_eligibility_decisions WHERE decision_id=?", (decision_id,)).fetchone())
