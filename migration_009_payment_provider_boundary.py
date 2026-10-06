"""Forward-only Production payment bindings; historical rows are not backfilled."""
from datetime import datetime, timezone
import db

MIGRATION_VERSION = 9
MIGRATION_NAME = "payment_provider_boundary"

def apply_schema(conn):
    conn.execute('SELECT json_valid(?)', ('{}',)).fetchone()
    existing = conn.execute("SELECT migration_name FROM schema_meta WHERE version=9").fetchone()
    if existing:
        if existing[0] != MIGRATION_NAME:
            raise RuntimeError("Conflicting migration 009")
        return
    conn.execute("""CREATE TABLE payment_obligations (
        obligation_id TEXT PRIMARY KEY,
        task_id TEXT NOT NULL,
        eligibility_decision_id TEXT NOT NULL REFERENCES settlement_eligibility_decisions(decision_id),
        contract_acceptance_decision_id TEXT NOT NULL REFERENCES contract_acceptance_decisions(decision_id),
        provider TEXT NOT NULL CHECK(provider='paypal'),
        environment TEXT NOT NULL CHECK(environment='PRODUCTION'),
        payee_id TEXT NOT NULL,
        amount_cents INTEGER NOT NULL CHECK(typeof(amount_cents)='integer' AND amount_cents>0),
        currency TEXT NOT NULL,
        created_at TEXT NOT NULL)""")
    conn.execute("""CREATE TABLE payment_order_bindings (
        obligation_id TEXT PRIMARY KEY REFERENCES payment_obligations(obligation_id),
        order_id TEXT NOT NULL UNIQUE,
        create_request_id TEXT NOT NULL UNIQUE,
        authorize_request_id TEXT NOT NULL UNIQUE,
        created_at TEXT NOT NULL)""")
    conn.execute("""CREATE TABLE payment_provider_evidence (
        evidence_id TEXT PRIMARY KEY,
        obligation_id TEXT NOT NULL UNIQUE REFERENCES payment_obligations(obligation_id),
        order_id TEXT NOT NULL UNIQUE,
        provider_authorization_id TEXT NOT NULL UNIQUE,
        authorization_event_id TEXT NOT NULL UNIQUE REFERENCES payment_authorization_events(event_id),
        payment_authorization_id TEXT NOT NULL UNIQUE REFERENCES payment_authorizations(authorization_id),
        environment TEXT NOT NULL CHECK(environment='PRODUCTION'),
        endpoint TEXT NOT NULL CHECK(endpoint='https://api-m.paypal.com'),
        payee_id TEXT NOT NULL,
        amount_cents INTEGER NOT NULL,
        currency TEXT NOT NULL,
        observation_json TEXT NOT NULL,
        observation_sha256 TEXT NOT NULL,
        observed_at TEXT NOT NULL)""")
    conn.execute("""CREATE TABLE payment_operation_claims (
        obligation_id TEXT NOT NULL REFERENCES payment_obligations(obligation_id),
        operation TEXT NOT NULL CHECK(operation IN ('create','authorize')),
        request_id TEXT NOT NULL UNIQUE,
        claimed_at TEXT NOT NULL,
        PRIMARY KEY(obligation_id,operation))""")
    conn.execute("""CREATE TABLE payment_capture_evidence (
        settlement_id TEXT PRIMARY KEY REFERENCES canonical_settlements(settlement_id),
        capture_id TEXT NOT NULL UNIQUE,
        environment TEXT NOT NULL CHECK(environment='PRODUCTION'),
        endpoint TEXT NOT NULL CHECK(endpoint='https://api-m.paypal.com'),
        observation_json TEXT NOT NULL,
        observation_sha256 TEXT NOT NULL,
        observed_at TEXT NOT NULL,
        provenance_source TEXT NOT NULL CHECK(provenance_source IN ('capture_response','capture_retrieval')))""")
    conn.execute("""CREATE TRIGGER production_capture_evidence_binding BEFORE INSERT ON payment_capture_evidence
        WHEN NOT EXISTS(SELECT 1 FROM canonical_settlements s
          JOIN payment_provider_evidence p ON p.payment_authorization_id=s.payment_authorization_id
          WHERE s.settlement_id=NEW.settlement_id AND s.environment=NEW.environment
          AND p.obligation_id=s.obligation_id AND p.environment=NEW.environment
          AND EXISTS(SELECT 1 FROM canonical_capture_attempts c WHERE c.settlement_id=s.settlement_id)
          AND json_extract(NEW.observation_json,'$.provenance_source')=NEW.provenance_source
          AND json_extract(NEW.observation_json,'$.capture_id')=NEW.capture_id
          AND json_extract(NEW.observation_json,'$.authorization_id')=p.provider_authorization_id
          AND json_extract(NEW.observation_json,'$.order_id')=p.order_id
          AND json_extract(NEW.observation_json,'$.payee_id')=p.payee_id
          AND json_extract(NEW.observation_json,'$.amount_cents')=s.amount_cents
          AND json_extract(NEW.observation_json,'$.currency')=s.currency
          AND json_extract(NEW.observation_json,'$.environment')=NEW.environment
          AND json_extract(NEW.observation_json,'$.endpoint')=NEW.endpoint
          AND json_extract(NEW.observation_json,'$.status')='COMPLETED'
          AND json_extract(NEW.observation_json,'$.final_capture')=1)
        BEGIN SELECT RAISE(ABORT,'Capture evidence binding mismatch'); END""")
    conn.execute("""CREATE TRIGGER production_capture_confirmation_guard BEFORE UPDATE OF state,provider_capture_id
        ON canonical_settlements WHEN NEW.environment='PRODUCTION' AND (
          (OLD.provider_capture_id IS NOT NULL AND NEW.provider_capture_id IS NOT OLD.provider_capture_id)
          OR ((NEW.provider_capture_id IS NOT NULL OR NEW.state IN ('PROVIDER_CONFIRMED','REVENUE_RECORDED'))
              AND NOT EXISTS(SELECT 1 FROM payment_capture_evidence p
                WHERE p.settlement_id=NEW.settlement_id AND p.capture_id=NEW.provider_capture_id)))
        BEGIN SELECT RAISE(ABORT,'Immutable authoritative capture proof required'); END""")
    conn.execute("""CREATE TRIGGER payment_obligation_eligibility BEFORE INSERT ON payment_obligations
        WHEN NOT EXISTS(SELECT 1 FROM settlement_eligibility_decisions q
          WHERE q.decision_id=NEW.eligibility_decision_id AND q.approved=1
          AND q.task_id=NEW.task_id AND q.environment=NEW.environment
          AND q.contract_acceptance_decision_id=NEW.contract_acceptance_decision_id)
        BEGIN SELECT RAISE(ABORT,'Payment obligation eligibility mismatch'); END""")
    conn.execute("""CREATE TRIGGER payment_provider_binding BEFORE INSERT ON payment_provider_evidence
        WHEN NOT EXISTS(SELECT 1 FROM payment_obligations o
          JOIN payment_order_bindings b ON b.obligation_id=o.obligation_id
          JOIN payment_authorizations a ON a.authorization_id=NEW.payment_authorization_id
          JOIN payment_authorization_events e ON e.event_id=NEW.authorization_event_id
          JOIN payment_authorization_verifications v ON v.authorization_event_id=e.event_id
          WHERE o.obligation_id=NEW.obligation_id AND b.order_id=NEW.order_id
          AND o.payee_id=NEW.payee_id AND o.amount_cents=NEW.amount_cents AND o.currency=NEW.currency
          AND o.environment=NEW.environment AND a.environment=NEW.environment AND e.environment=NEW.environment
          AND a.task_id=o.task_id AND a.contract_acceptance_decision_id=o.contract_acceptance_decision_id
          AND a.provider='paypal' AND e.provider='paypal' AND a.verified=1 AND e.authenticated=1
          AND a.provider_authorization_id=NEW.provider_authorization_id
          AND e.provider_authorization_id=NEW.provider_authorization_id
          AND a.amount_cents=o.amount_cents AND a.currency=o.currency
          AND e.amount_cents=o.amount_cents AND e.currency=o.currency
          AND v.verified=1 AND v.task_id=o.task_id AND v.environment=o.environment
          AND v.contract_acceptance_decision_id=o.contract_acceptance_decision_id)
        BEGIN SELECT RAISE(ABORT,'Provider evidence binding mismatch'); END""")
    conn.execute("""CREATE TRIGGER production_settlement_payment_binding BEFORE INSERT ON canonical_settlements
        WHEN NEW.environment='PRODUCTION' AND NOT EXISTS(
          SELECT 1 FROM payment_obligations o JOIN payment_provider_evidence p ON p.obligation_id=o.obligation_id
          WHERE o.obligation_id=NEW.obligation_id AND o.task_id=NEW.task_id
          AND o.eligibility_decision_id=NEW.eligibility_decision_id
          AND o.contract_acceptance_decision_id=NEW.contract_acceptance_decision_id
          AND p.payment_authorization_id=NEW.payment_authorization_id
          AND o.environment=NEW.environment AND o.provider=NEW.provider
          AND o.amount_cents=NEW.amount_cents AND o.currency=NEW.currency)
        BEGIN SELECT RAISE(ABORT,'Production payment binding required'); END""")
    conn.execute("""CREATE TRIGGER production_settlement_terms_immutable
        BEFORE UPDATE ON canonical_settlements WHEN OLD.environment='PRODUCTION' AND (
          NEW.obligation_id IS NOT OLD.obligation_id OR NEW.payment_authorization_id IS NOT OLD.payment_authorization_id
          OR NEW.amount_cents IS NOT OLD.amount_cents OR NEW.currency IS NOT OLD.currency
          OR NEW.provider IS NOT OLD.provider OR NEW.environment IS NOT OLD.environment)
        BEGIN SELECT RAISE(ABORT,'Immutable Production financial terms'); END""")
    for table,key in (('payment_obligations','obligation_id'),('payment_order_bindings','obligation_id'),
                      ('payment_provider_evidence','evidence_id'),('payment_operation_claims','request_id'),
                      ('payment_capture_evidence','settlement_id')):
        for action in ('UPDATE','DELETE'):
            conn.execute(f"CREATE TRIGGER payment_boundary_{table}_{action.lower()} BEFORE {action} ON {table} "
                         "BEGIN SELECT RAISE(ABORT,'Immutable payment boundary'); END")
        conflicts = {
            'payment_obligations': 'obligation_id=NEW.obligation_id',
            'payment_order_bindings': 'obligation_id=NEW.obligation_id OR order_id=NEW.order_id OR create_request_id=NEW.create_request_id OR authorize_request_id=NEW.authorize_request_id',
            'payment_provider_evidence': 'evidence_id=NEW.evidence_id OR obligation_id=NEW.obligation_id OR order_id=NEW.order_id OR provider_authorization_id=NEW.provider_authorization_id OR authorization_event_id=NEW.authorization_event_id OR payment_authorization_id=NEW.payment_authorization_id',
            'payment_operation_claims': 'request_id=NEW.request_id OR (obligation_id=NEW.obligation_id AND operation=NEW.operation)',
            'payment_capture_evidence': 'settlement_id=NEW.settlement_id OR capture_id=NEW.capture_id',
        }[table]
        conn.execute(f"CREATE TRIGGER payment_boundary_{table}_replace BEFORE INSERT ON {table} "
                     f"WHEN EXISTS(SELECT 1 FROM {table} WHERE {conflicts}) "
                     "BEGIN SELECT RAISE(ABORT,'Payment boundary replacement forbidden'); END")
    for table,key,ref in (('payment_authorizations','authorization_id','payment_authorization_id'),
                          ('payment_authorization_events','event_id','authorization_event_id')):
        for action in ('UPDATE','DELETE'):
            conn.execute(f"CREATE TRIGGER payment_boundary_freeze_{table}_{action.lower()} BEFORE {action} ON {table} "
                         f"WHEN EXISTS(SELECT 1 FROM payment_provider_evidence WHERE {ref}=OLD.{key}) "
                         "BEGIN SELECT RAISE(ABORT,'Immutable provider-linked payment record'); END")
        conflict = ("old.authorization_id=NEW.authorization_id OR "
                    "(old.provider=NEW.provider AND old.provider_authorization_id=NEW.provider_authorization_id)"
                    if table=='payment_authorizations' else
                    "old.event_id=NEW.event_id OR (old.provider=NEW.provider AND old.provider_event_id=NEW.provider_event_id AND old.environment=NEW.environment)")
        conn.execute(f"CREATE TRIGGER payment_boundary_freeze_{table}_replace BEFORE INSERT ON {table} "
                     f"WHEN EXISTS(SELECT 1 FROM payment_provider_evidence p JOIN {table} old ON p.{ref}=old.{key} WHERE {conflict}) "
                     "BEGIN SELECT RAISE(ABORT,'Provider-linked payment replacement forbidden'); END")
    for action in ('UPDATE','DELETE'):
        conn.execute(f"CREATE TRIGGER payment_boundary_verification_{action.lower()} BEFORE {action} ON payment_authorization_verifications "
            "WHEN EXISTS(SELECT 1 FROM payment_provider_evidence WHERE authorization_event_id=OLD.authorization_event_id) "
            "BEGIN SELECT RAISE(ABORT,'Immutable provider verification'); END")
    conn.execute("""CREATE TRIGGER payment_boundary_verification_replace BEFORE INSERT ON payment_authorization_verifications
        WHEN EXISTS(SELECT 1 FROM payment_provider_evidence p JOIN payment_authorization_verifications v
          ON v.authorization_event_id=p.authorization_event_id WHERE v.verification_id=NEW.verification_id)
        BEGIN SELECT RAISE(ABORT,'Provider verification replacement forbidden'); END""")
    conn.execute("""CREATE TRIGGER production_capture_claim_no_delete BEFORE DELETE ON canonical_capture_attempts
        BEGIN SELECT RAISE(ABORT,'Durable capture claim cannot be deleted'); END""")
    conn.execute("""CREATE TRIGGER production_capture_claim_identity_immutable BEFORE UPDATE ON canonical_capture_attempts
        WHEN NEW.settlement_id IS NOT OLD.settlement_id OR NEW.request_id IS NOT OLD.request_id
          OR NEW.claimed_at IS NOT OLD.claimed_at
        BEGIN SELECT RAISE(ABORT,'Immutable durable capture claim'); END""")
    conn.execute("""CREATE TRIGGER production_capture_claim_no_replace BEFORE INSERT ON canonical_capture_attempts
        WHEN EXISTS(SELECT 1 FROM canonical_capture_attempts
          WHERE settlement_id IS NEW.settlement_id OR request_id=NEW.request_id)
        BEGIN SELECT RAISE(ABORT,'Capture claim replacement forbidden'); END""")
    conn.execute("INSERT INTO schema_meta VALUES(?,?,?)",
                 (MIGRATION_VERSION,MIGRATION_NAME,datetime.now(timezone.utc).isoformat()))

def migrate():
    with db.get_db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        apply_schema(conn)
        conn.commit()

if __name__ == '__main__':
    raise SystemExit("Explicit reviewed upgrade required; no automatic live migration")
