import json
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, Any, Optional, List
from db import get_db
from service_principals import compatibility_environment

class ValidationStatus(str, Enum):
    PASSED       = "PASSED"        # All checks executed and satisfied
    FAILED       = "FAILED"        # Explicit assertion or test suite failure
    ERROR        = "ERROR"         # Execution, AST, or test-runner exception
    SKIPPED      = "SKIPPED"       # Prerequisite missing (e.g., no test targets)
    INCONCLUSIVE = "INCONCLUSIVE"  # Evidence incomplete or ambiguous

@dataclass
class RawEvidence:
    task_id: str
    git_commit_hash: str
    pytest_exit_code: Optional[int]
    syntax_valid: bool
    compilation_valid: bool
    diff_policy_passed: bool
    raw_payload: Dict[str, Any]
    evidence_id: str = field(default_factory=lambda: f"ev_{uuid.uuid4().hex[:12]}")
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

@dataclass
class ValidationResult:
    validator_id: str
    evidence_id: str
    status: ValidationStatus
    checks: Dict[str, ValidationStatus]
    confidence: Optional[float] = None  # None until calibrated against telemetry
    error_details: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

class LocalValidator:
    def __init__(self, validator_id=None, *, environment=None):
        # Legacy evaluator is compatibility-only; canonical production uses validate_work.
        compatibility_environment(environment, operation="local_validator")
        self.validator_id = validator_id or "local_precision_v1"

    def evaluate(self, evidence: RawEvidence) -> ValidationResult:
        return _evaluate_recorded_evidence(evidence, self.validator_id)


def _evaluate_recorded_evidence(evidence: RawEvidence, validator_id: str) -> ValidationResult:
    checks: Dict[str, ValidationStatus] = {}

    # 1. Syntax Check
    checks["syntax"] = ValidationStatus.PASSED if evidence.syntax_valid else ValidationStatus.FAILED

    # 2. Compilation Check
    checks["compilation"] = ValidationStatus.PASSED if evidence.compilation_valid else ValidationStatus.FAILED

    # 3. Diff Policy Check
    checks["diff_policy"] = ValidationStatus.PASSED if evidence.diff_policy_passed else ValidationStatus.FAILED

    # 4. Pytest Test Suite Check
    if evidence.pytest_exit_code is None:
        checks["pytest"] = ValidationStatus.SKIPPED
    elif evidence.pytest_exit_code == 0:
        checks["pytest"] = ValidationStatus.PASSED
    else:
        checks["pytest"] = ValidationStatus.FAILED

    # Determine overall status across checks
    if any(v == ValidationStatus.FAILED for v in checks.values()):
        overall_status = ValidationStatus.FAILED
    elif checks.get("pytest") == ValidationStatus.SKIPPED and all(v == ValidationStatus.PASSED for k, v in checks.items() if k != "pytest"):
        overall_status = ValidationStatus.INCONCLUSIVE
    elif all(v == ValidationStatus.PASSED for v in checks.values()):
        overall_status = ValidationStatus.PASSED
    else:
        overall_status = ValidationStatus.ERROR

    return ValidationResult(
        validator_id=validator_id,
        evidence_id=evidence.evidence_id,
        status=overall_status,
        checks=checks,
        confidence=None  # Explicitly None per contract until telemetry calibration
    )


def save_evidence_and_result(evidence: RawEvidence, result: ValidationResult, *, environment=None):
    """Compatibility-only persistence; never a production validator identity entry point."""
    compatibility_environment(environment, operation="save_evidence_and_result")
    if result.evidence_id != evidence.evidence_id:
        raise ValueError("Validation result must reference exact evidence")
    with get_db() as conn:
        if conn.execute("SELECT 1 FROM work_deliveries WHERE task_id=? AND environment='PRODUCTION' LIMIT 1",
                (evidence.task_id,)).fetchone():
            raise ValueError("Compatibility persistence cannot write production work")
        cursor = conn.cursor()

        # Record immutable raw evidence
        cursor.execute("""
            INSERT INTO raw_evidence (
                evidence_id, task_id, git_commit_hash, pytest_exit_code,
                syntax_valid, compilation_valid, diff_policy_passed, raw_payload, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            evidence.evidence_id,
            evidence.task_id,
            evidence.git_commit_hash,
            evidence.pytest_exit_code,
            evidence.syntax_valid,
            evidence.compilation_valid,
            evidence.diff_policy_passed,
            json.dumps(evidence.raw_payload),
            evidence.created_at
        ))

        # Record validation result
        cursor.execute("""
            INSERT INTO validation_results (
                validator_id, evidence_id, status, checks_json, confidence, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
        """, (
            result.validator_id,
            result.evidence_id,
            result.status.value,
            json.dumps({k: v.value for k, v in result.checks.items()}),
            result.confidence,
            result.created_at
        ))

        conn.commit()

if __name__ == "__main__":
    # Self-test execution
    sample_evidence = RawEvidence(
        task_id="task_live_001",
        git_commit_hash="a1b2c3d4e5f",
        pytest_exit_code=0,
        syntax_valid=True,
        compilation_valid=True,
        diff_policy_passed=True,
        raw_payload={"runner": "pytest 8.1.1", "duration_sec": 1.42}
    )

    validator = LocalValidator(environment="TEST")
    val_result = validator.evaluate(sample_evidence)
    save_evidence_and_result(sample_evidence, val_result, environment="TEST")

    print(f"Validation logged: Status={val_result.status.value}, EvidenceID={sample_evidence.evidence_id}")