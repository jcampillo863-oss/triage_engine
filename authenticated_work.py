"""Narrow production work/evidence and validation services. No acceptance/payment calls."""
from datetime import datetime, timezone
import hashlib
import json
import db
from service_principals import (UNSET, PRODUCE_WORK, VALIDATE_WORK, PRODUCER_ID,
    authenticate, audit_authority, PrincipalAuthenticationError, reject_credential_material)
from validation import RawEvidence, _evaluate_recorded_evidence, ValidationStatus

POLICY_NAME = "canonical_local_precision"
POLICY_VERSION = "1"

class AuthorityOperationError(ValueError):
    pass


def _production(environment):
    if environment != "PRODUCTION":
        raise AuthorityOperationError("This authority operation requires PRODUCTION")


def _delivery(conn, delivery_id):
    row = conn.execute("SELECT * FROM work_deliveries WHERE delivery_id=?", (delivery_id,)).fetchone()
    if row is None or row["environment"] != "PRODUCTION" or row["status"] != "DISPATCHED":
        raise AuthorityOperationError("Production work delivery is unavailable")
    if row["producer_id"] != PRODUCER_ID or not row["dispatched_commit_hash"]:
        raise AuthorityOperationError("Authenticated producer provenance is required")
    return row


def record_work_evidence(*, producer_credential=None, work_delivery_id, evidence_id,
        pytest_exit_code, syntax_valid, compilation_valid, diff_policy_passed,
        raw_payload=None, environment="PRODUCTION", producer_id=UNSET):
    """Identity, task and commit come from authentication plus immutable delivery binding."""
    principal = authenticate(producer_credential, PRODUCE_WORK, operation="record_work_evidence")
    try:
        _production(environment)
        if producer_id is not UNSET:
            raise PrincipalAuthenticationError("Caller producer identity is forbidden")
        for value in (work_delivery_id, evidence_id):
            if not isinstance(value, str) or not value or value != value.strip():
                raise AuthorityOperationError("Invalid evidence binding identity")
        if pytest_exit_code is not None and (isinstance(pytest_exit_code, bool) or not isinstance(pytest_exit_code, int)):
            raise AuthorityOperationError("Invalid test exit code")
        if not all(isinstance(flag, bool) for flag in (syntax_valid, compilation_valid, diff_policy_passed)):
            raise AuthorityOperationError("Explicit boolean evidence is required")
        reject_credential_material(dict(work_delivery_id=work_delivery_id,evidence_id=evidence_id,raw_payload=raw_payload))
        payload = json.dumps(raw_payload, sort_keys=True, separators=(",", ":"))
        with db.get_db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            delivery = _delivery(conn, work_delivery_id)
            fields=dict(evidence_id=evidence_id, task_id=delivery["task_id"], git_commit_hash=delivery["dispatched_commit_hash"],
                pytest_exit_code=pytest_exit_code, syntax_valid=int(syntax_valid), compilation_valid=int(compilation_valid),
                diff_policy_passed=int(diff_policy_passed), raw_payload=payload)
            reject_credential_material(fields)
            existing=conn.execute("SELECT * FROM raw_evidence WHERE evidence_id=?",(evidence_id,)).fetchone()
            if existing:
                if any(existing[key] != value for key,value in fields.items()):
                    raise AuthorityOperationError("Evidence replay conflicts with recorded observations")
            else:
                fields["created_at"]=datetime.now(timezone.utc).isoformat()
                columns=list(fields)
                conn.execute("INSERT INTO raw_evidence ("+",".join(columns)+") VALUES ("+
                    ",".join(":"+column for column in columns)+")",fields)
            conn.commit()
        audit_authority(principal.principal_id, PRODUCE_WORK, "record_work_evidence", "succeeded")
        return {"evidence_id":evidence_id,"work_delivery_id":work_delivery_id,"producer_id":principal.principal_id}
    except Exception:
        audit_authority(principal.principal_id, PRODUCE_WORK, "record_work_evidence", "rejected")
        raise


def validate_work(*, validator_credential=None, work_delivery_id, evidence_id,
        environment="PRODUCTION", validator_id=UNSET):
    """No caller validator ID, ValidationResult, accepted flag or technical decision is admitted."""
    principal = authenticate(validator_credential, VALIDATE_WORK, operation="validate_work")
    try:
        _production(environment)
        if validator_id is not UNSET:
            raise PrincipalAuthenticationError("Caller validator identity is forbidden")
        for value in (work_delivery_id,evidence_id):
            if not isinstance(value,str) or not value or value != value.strip():
                raise AuthorityOperationError("Invalid validation binding identity")
        reject_credential_material(dict(work_delivery_id=work_delivery_id,evidence_id=evidence_id))
        with db.get_db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            delivery=_delivery(conn,work_delivery_id)
            if delivery["producer_id"] == principal.principal_id:
                raise AuthorityOperationError("Producer cannot validate its own work")
            evidence=conn.execute("SELECT * FROM raw_evidence WHERE evidence_id=?",(evidence_id,)).fetchone()
            if evidence is None or evidence["task_id"] != delivery["task_id"] or evidence["git_commit_hash"] != delivery["dispatched_commit_hash"]:
                raise AuthorityOperationError("Evidence does not match the dispatched work")
            if any(evidence[column] not in (0,1) for column in ('syntax_valid','compilation_valid','diff_policy_passed')):
                raise AuthorityOperationError("Recorded evidence flags are invalid")
            if evidence["pytest_exit_code"] is not None and not isinstance(evidence["pytest_exit_code"],int):
                raise AuthorityOperationError("Recorded test exit code is invalid")
            # Do not expose arbitrary raw bodies or potentially sensitive content in responses.
            reject_credential_material(dict(evidence))
            existing=conn.execute("""SELECT t.*,v.validator_id,v.evidence_id AS validated_evidence,v.status
                FROM technical_decisions t JOIN validation_results v ON v.result_id=t.validation_result_id
                WHERE t.evidence_id=? AND t.policy_name=? AND t.policy_version=?""",
                (evidence_id,POLICY_NAME,POLICY_VERSION)).fetchone()
            if existing:
                if (existing["environment"] != "PRODUCTION" or existing["validator_id"] != principal.principal_id
                    or existing["validated_evidence"] != evidence_id
                    or bool(existing["accepted"]) != (existing["status"] == 'PASSED')):
                    raise AuthorityOperationError("Technical decision replay conflicts with authenticated validation")
                result_id=existing["validation_result_id"]
                decision_id=existing["decision_id"]
                accepted=bool(existing["accepted"])
            else:
                observed=RawEvidence(task_id=evidence["task_id"],git_commit_hash=evidence["git_commit_hash"],
                    pytest_exit_code=evidence["pytest_exit_code"],syntax_valid=bool(evidence["syntax_valid"]),
                    compilation_valid=bool(evidence["compilation_valid"]),diff_policy_passed=bool(evidence["diff_policy_passed"]),
                    raw_payload={},evidence_id=evidence_id,created_at=evidence["created_at"])
                result=_evaluate_recorded_evidence(observed,principal.principal_id)
                accepted=result.status == ValidationStatus.PASSED
                result_id=conn.execute("""INSERT INTO validation_results(validator_id,evidence_id,status,checks_json,confidence,created_at)
                    VALUES(?,?,?,?,?,?)""",(principal.principal_id,evidence_id,result.status.value,
                    json.dumps({key:value.value for key,value in result.checks.items()},sort_keys=True),None,result.created_at)).lastrowid
                decision_id="tech_"+hashlib.sha256(f"{evidence_id}:{result_id}:{POLICY_NAME}:{POLICY_VERSION}".encode()).hexdigest()
                conn.execute("""INSERT INTO technical_decisions(decision_id,evidence_id,validation_result_id,policy_name,
                    policy_version,accepted,rejection_reasons,environment,created_at) VALUES(?,?,?,?,?,?,?,'PRODUCTION',?)""",
                    (decision_id,evidence_id,result_id,POLICY_NAME,POLICY_VERSION,int(accepted),
                    json.dumps([] if accepted else ['Recorded technical validation did not pass']),result.created_at))
            conn.commit()
        audit_authority(principal.principal_id, VALIDATE_WORK, "validate_work", "succeeded")
        return dict(validator_id=principal.principal_id,evidence_id=evidence_id,validation_result_id=result_id,
            technical_decision_id=decision_id,accepted=accepted,environment="PRODUCTION")
    except Exception:
        audit_authority(principal.principal_id, VALIDATE_WORK, "validate_work", "rejected")
        raise
