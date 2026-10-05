import json
from typing import Dict, Any, Optional
from db import init_db
from validation import RawEvidence, LocalValidator, save_evidence_and_result, ValidationStatus
from acceptance_policy import PolicyEvaluator, AcceptancePolicy, save_policy_decision
from revenue_engine import RevenueEngine, SettlementState

class TriagePipeline:
    def __init__(self, policy: AcceptancePolicy = AcceptancePolicy.GITHUB_MERGE_V1, *, environment=None):
        self.validator = LocalValidator(environment=environment)
        self.environment = environment
        self.policy_evaluator = PolicyEvaluator(policy=policy)
        self.revenue_engine = RevenueEngine()

    def process_task(
        self,
        task_id: str,
        git_commit_hash: str,
        pytest_exit_code: Optional[int],
        syntax_valid: bool,
        compilation_valid: bool,
        diff_policy_passed: bool,
        amount_cents: int,
        raw_payload: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Runs an execution payload through validation, acceptance decisioning, and payment capture."""
        payload = raw_payload or {}

        # 1. Ingest immutable raw evidence
        evidence = RawEvidence(
            task_id=task_id,
            git_commit_hash=git_commit_hash,
            pytest_exit_code=pytest_exit_code,
            syntax_valid=syntax_valid,
            compilation_valid=compilation_valid,
            diff_policy_passed=diff_policy_passed,
            raw_payload=payload
        )

        # 2. Evaluate 5-state validation
        val_result = self.validator.evaluate(evidence)
        save_evidence_and_result(evidence, val_result, environment=self.environment)

        # 3. Apply acceptance policy rules
        decision = self.policy_evaluator.evaluate(val_result)
        save_policy_decision(decision, environment=self.environment)

        # 4. Trigger settlement lifecycle if accepted
        settlement_info = None
        if decision.accepted:
            intent = self.revenue_engine.create_settlement_intent(
                task_id=task_id,
                amount_cents=amount_cents,
                decision_id=decision.decision_id
            )

            # Transition to CAPTURE_REQUESTED pending payment provider webhook confirmation
            captured = self.revenue_engine.transition_state(
                intent.settlement_id,
                SettlementState.CAPTURE_REQUESTED
            )
            settlement_info = {
                "settlement_id": captured.settlement_id,
                "state": captured.state.value,
                "amount_cents": captured.amount_cents
            }

        return {
            "task_id": task_id,
            "evidence_id": evidence.evidence_id,
            "validation_status": val_result.status.value,
            "decision_id": decision.decision_id,
            "accepted": decision.accepted,
            "rejection_reasons": decision.rejection_reasons,
            "settlement": settlement_info
        }

if __name__ == "__main__":
    init_db()
    pipeline = TriagePipeline(environment="TEST")

    print("=== Test 1: Successful Validation & Acceptance ===")
    success = pipeline.process_task(
        task_id="task_live_002",
        git_commit_hash="c0ff3312345",
        pytest_exit_code=0,
        syntax_valid=True,
        compilation_valid=True,
        diff_policy_passed=True,
        amount_cents=2500,
        raw_payload={"runner": "precision_executor_v1", "duration_sec": 0.88}
    )
    print(json.dumps(success, indent=2))

    print("\n=== Test 2: Failed Execution (Pytest Exit 1) ===")
    failure = pipeline.process_task(
        task_id="task_live_003",
        git_commit_hash="f411ed00000",
        pytest_exit_code=1,
        syntax_valid=True,
        compilation_valid=True,
        diff_policy_passed=True,
        amount_cents=2500,
        raw_payload={"runner": "precision_executor_v1", "duration_sec": 1.12}
    )
    print(json.dumps(failure, indent=2))