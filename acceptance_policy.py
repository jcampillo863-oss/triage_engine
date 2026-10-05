"""Legacy compatibility only; acceptance_decisions is not canonical technical/contractual authority."""
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from db import get_db
from service_principals import compatibility_environment
from validation import ValidationResult, ValidationStatus

class AcceptancePolicy(str, Enum):
    GITHUB_MERGE_V1 = "github_merge_v1"
    STRICT_ZERO_FAILURE = "strict_zero_failure"

@dataclass
class PolicyDecision:
    evidence_id: str
    policy_name: str = AcceptancePolicy.GITHUB_MERGE_V1.value
    accepted: bool = False
    rejection_reasons: Optional[str] = None
    decision_id: str = field(default_factory=lambda: f"dec_{uuid.uuid4().hex[:12]}")
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

class PolicyEvaluator:
    def __init__(self, policy: AcceptancePolicy = AcceptancePolicy.GITHUB_MERGE_V1):
        self.policy = policy

    def evaluate(self, result: ValidationResult) -> PolicyDecision:
        reasons = []

        if self.policy == AcceptancePolicy.GITHUB_MERGE_V1:
            # Rule 1: Validator must give overall status of PASSED
            if result.status != ValidationStatus.PASSED:
                reasons.append(f"Overall validation status is {result.status.value}, expected PASSED.")

            # Rule 2: Syntax, Compilation, and Diff Policy checks are mandatory
            if result.checks.get("syntax") != ValidationStatus.PASSED:
                reasons.append("Syntax check failed.")

            if result.checks.get("compilation") != ValidationStatus.PASSED:
                reasons.append("Compilation check failed.")

            if result.checks.get("diff_policy") != ValidationStatus.PASSED:
                reasons.append("Diff policy check failed.")

            # Rule 3: Pytest failure blocks acceptance
            if result.checks.get("pytest") == ValidationStatus.FAILED:
                reasons.append("Pytest test suite failed.")

        accepted = len(reasons) == 0
        rejection_str = "; ".join(reasons) if reasons else None

        return PolicyDecision(
            evidence_id=result.evidence_id,
            policy_name=self.policy.value,
            accepted=accepted,
            rejection_reasons=rejection_str
        )

def save_policy_decision(decision: PolicyDecision, *, environment=None):
    """Write legacy conclusions only in an explicitly selected compatibility environment."""
    compatibility_environment(environment, operation="legacy_policy")
    with get_db() as conn:
        if conn.execute("""SELECT 1 FROM raw_evidence e JOIN work_deliveries d ON d.task_id=e.task_id
            WHERE e.evidence_id=? AND d.environment='PRODUCTION' LIMIT 1""",(decision.evidence_id,)).fetchone():
            raise ValueError("Legacy policy cannot write production conclusions")
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO acceptance_decisions (
                decision_id, evidence_id, policy_name, accepted, rejection_reasons, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
        """, (
            decision.decision_id,
            decision.evidence_id,
            decision.policy_name,
            1 if decision.accepted else 0,
            decision.rejection_reasons,
            decision.created_at
        ))
        conn.commit()

if __name__ == "__main__":
    # Test using a mock ValidationResult
    mock_result = ValidationResult(
        validator_id="local_precision_v1",
        evidence_id="ev_12ae4e9cafe9",
        status=ValidationStatus.PASSED,
        checks={
            "syntax": ValidationStatus.PASSED,
            "compilation": ValidationStatus.PASSED,
            "diff_policy": ValidationStatus.PASSED,
            "pytest": ValidationStatus.PASSED
        }
    )

    evaluator = PolicyEvaluator(policy=AcceptancePolicy.GITHUB_MERGE_V1)
    decision = evaluator.evaluate(mock_result)
    save_policy_decision(decision, environment="TEST")

    print(f"Policy Decision logged: DecisionID={decision.decision_id}, Accepted={decision.accepted}")