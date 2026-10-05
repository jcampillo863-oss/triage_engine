"""Explicit synthetic test fixtures; never used by production authorities."""
import uuid
from datetime import datetime, timezone
from settlement_eligibility import approve_settlement_eligibility


def seed_chain(conn, *, task_id=None, acceptance_id=None, event_id=None,
        producer_id="test-producer", validator_id="test-validator",
        commit="a"*40, evidence_commit="a"*40, environment="TEST"):
    suffix=uuid.uuid4().hex
    task_id=task_id or "eligibility-test-task-"+suffix
    delivery_id="eligibility-test-delivery-"+suffix
    evidence_id="eligibility-test-evidence-"+suffix
    technical_id="eligibility-test-technical-"+suffix
    acceptance_id=acceptance_id or "eligibility-test-contract-"+suffix
    event_id=event_id or "eligibility-test-event-"+suffix
    stamp=datetime.now(timezone.utc).isoformat()
    repo="example-owner/example-repo"
    pr=int(suffix[:10],16)+1
    url=f"https://github.com/{repo}/pull/{pr}"
    conn.execute("""INSERT INTO work_deliveries(delivery_id,task_id,repository,pull_request_number,
        pull_request_url,producer_id,dispatched_commit_hash,status,environment,dispatched_at,created_at,updated_at)
        VALUES(?,?,?,?,?,?,?,'DISPATCHED',?,?,?,?)""",
        (delivery_id,task_id,repo,pr,url,producer_id,commit,environment,stamp,stamp,stamp))
    conn.execute("""INSERT INTO raw_evidence(evidence_id,task_id,git_commit_hash,pytest_exit_code,
        syntax_valid,compilation_valid,diff_policy_passed,raw_payload,created_at)
        VALUES(?,?,?,0,1,1,1,'{}',?)""",(evidence_id,task_id,evidence_commit,stamp))
    validation_id=conn.execute("""INSERT INTO validation_results(validator_id,evidence_id,status,checks_json,created_at)
        VALUES(?,?,'PASSED','{}',?)""",(validator_id,evidence_id,stamp)).lastrowid
    conn.execute("""INSERT INTO technical_decisions(decision_id,evidence_id,validation_result_id,
        policy_name,policy_version,accepted,environment,created_at)
        VALUES(?,?,?,'synthetic-test-policy','1',1,?,?)""",(technical_id,evidence_id,validation_id,environment,stamp))
    existing=conn.execute('SELECT 1 FROM external_acceptance_events WHERE event_id=?',(event_id,)).fetchone()
    if existing:
        conn.execute("""UPDATE external_acceptance_events SET work_delivery_id=?,repository=?,pull_request_number=?,pull_request_url=?
            WHERE event_id=?""",(delivery_id,repo,pr,url,event_id))
    else:
        conn.execute("""INSERT INTO external_acceptance_events(event_id,task_id,source,external_event_id,event_type,
            repository,pull_request_number,pull_request_url,authenticated,work_delivery_id,environment,observed_at,created_at)
            VALUES(?,?,'TEST_HARNESS',?,'TEST_ACCEPTANCE',?,?,?,1,?,?,?,?)""",
            (event_id,task_id,event_id,repo,pr,url,delivery_id,environment,stamp,stamp))
    if not conn.execute('SELECT 1 FROM contract_acceptance_decisions WHERE decision_id=?',(acceptance_id,)).fetchone():
        conn.execute("""INSERT INTO contract_acceptance_decisions(decision_id,task_id,acceptance_event_id,policy_name,
            policy_version,accepted,environment,created_at) VALUES(?,?,?,'test-contract-policy','1',1,?,?)""",
            (acceptance_id,task_id,event_id,environment,stamp))
    return dict(task_id=task_id,work_delivery_id=delivery_id,evidence_id=evidence_id,validation_result_id=validation_id,
        technical_decision_id=technical_id,contract_acceptance_decision_id=acceptance_id,environment=environment)


def seed_test_eligibility(conn, *, task_id, acceptance_id, event_id):
    chain=seed_chain(conn,task_id=task_id,acceptance_id=acceptance_id,event_id=event_id)
    return approve_settlement_eligibility(conn,**chain)["decision_id"]


def seed_authorization(conn, chain):
    identity="eligibility-test-auth-"+uuid.uuid4().hex
    stamp=datetime.now(timezone.utc).isoformat()
    conn.execute("""INSERT INTO payment_authorizations(authorization_id,task_id,contract_acceptance_decision_id,
        provider,provider_authorization_id,amount_cents,currency,provider_status,verified,environment,
        verified_at,created_at,updated_at) VALUES(?,?,?,'TEST_PROVIDER',?,100,'AUD','AUTHORIZED',1,?,?,?,?)""",
        (identity,chain['task_id'],chain['contract_acceptance_decision_id'],identity,chain['environment'],stamp,stamp,stamp))
    return identity
