"""Forward-only provenance and eligibility schema. No historical approvals/backfill."""
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from contextlib import closing
import db
from settlement_eligibility import CHAIN_QUERY

MIGRATION_VERSION = 8
MIGRATION_NAME = "canonical_settlement_eligibility"


def apply_schema(conn):
    """Apply inside caller transaction; never commit or infer historical provenance."""
    existing = conn.execute("SELECT migration_name FROM schema_meta WHERE version=?", (MIGRATION_VERSION,)).fetchone()
    if existing:
        if existing[0] != MIGRATION_NAME:
            raise RuntimeError("Migration version 008 is already assigned")
        return
    if "producer_id" not in [r[1] for r in conn.execute("PRAGMA table_info(work_deliveries)")]:
        conn.execute("ALTER TABLE work_deliveries ADD COLUMN producer_id TEXT")
    conn.execute("""CREATE TABLE settlement_eligibility_decisions (
        decision_id TEXT PRIMARY KEY,
        task_id TEXT NOT NULL,
        work_delivery_id TEXT NOT NULL REFERENCES work_deliveries(delivery_id),
        evidence_id TEXT NOT NULL REFERENCES raw_evidence(evidence_id),
        validation_result_id INTEGER NOT NULL REFERENCES validation_results(result_id),
        technical_decision_id TEXT NOT NULL REFERENCES technical_decisions(decision_id),
        contract_acceptance_decision_id TEXT NOT NULL REFERENCES contract_acceptance_decisions(decision_id),
        environment TEXT NOT NULL CHECK(environment IN ('TEST','SANDBOX','PRODUCTION')),
        producer_id TEXT NOT NULL, validator_id TEXT NOT NULL,
        dispatched_commit_hash TEXT NOT NULL,
        policy_name TEXT NOT NULL CHECK(policy_name='canonical_settlement_eligibility'),
        policy_version TEXT NOT NULL CHECK(policy_version='1'),
        approved INTEGER NOT NULL CHECK(approved=1),
        created_at TEXT NOT NULL,
        UNIQUE(task_id,work_delivery_id,evidence_id,validation_result_id,
               technical_decision_id,contract_acceptance_decision_id,environment)
    )""")
    if "eligibility_decision_id" not in [r[1] for r in conn.execute("PRAGMA table_info(canonical_settlements)")]:
        conn.execute("ALTER TABLE canonical_settlements ADD COLUMN eligibility_decision_id TEXT REFERENCES settlement_eligibility_decisions(decision_id)")
    # Null is frozen too: an incomplete historical delivery cannot be silently completed.
    conn.execute("""CREATE TRIGGER eligibility_delivery_provenance_immutable
        BEFORE UPDATE OF delivery_id, task_id, repository, pull_request_number, pull_request_url,
            environment, producer_id, dispatched_commit_hash ON work_deliveries
        WHEN NEW.delivery_id IS NOT OLD.delivery_id OR NEW.task_id IS NOT OLD.task_id
          OR NEW.repository IS NOT OLD.repository OR NEW.pull_request_number IS NOT OLD.pull_request_number
          OR NEW.pull_request_url IS NOT OLD.pull_request_url OR NEW.environment IS NOT OLD.environment
          OR NEW.producer_id IS NOT OLD.producer_id OR NEW.dispatched_commit_hash IS NOT OLD.dispatched_commit_hash
        BEGIN SELECT RAISE(ABORT, 'Immutable delivery provenance'); END""")
    # INSERT OR REPLACE must not bypass the update guard.
    conn.execute("""CREATE TRIGGER eligibility_delivery_no_replace BEFORE INSERT ON work_deliveries
        WHEN EXISTS(SELECT 1 FROM work_deliveries WHERE delivery_id=NEW.delivery_id
            OR (repository=NEW.repository AND pull_request_number=NEW.pull_request_number AND environment=NEW.environment))
        BEGIN SELECT RAISE(ABORT, 'Delivery replacement forbidden'); END""")
    params = ('task_id','work_delivery_id','evidence_id','validation_result_id','technical_decision_id',
        'contract_acceptance_decision_id','environment')
    predicate = CHAIN_QUERY
    for key in params:
        predicate = predicate.replace(':' + key, 'NEW.' + key)
    predicate += " AND d.producer_id=NEW.producer_id AND v.validator_id=NEW.validator_id AND d.dispatched_commit_hash=NEW.dispatched_commit_hash"
    conn.execute("CREATE TRIGGER eligibility_chain_guard BEFORE INSERT ON settlement_eligibility_decisions "
        "WHEN NOT EXISTS(" + predicate + ") BEGIN SELECT RAISE(ABORT, 'Invalid eligibility chain'); END")
    conn.execute("""CREATE TRIGGER eligibility_artifact_no_replace BEFORE INSERT ON settlement_eligibility_decisions
        WHEN EXISTS(SELECT 1 FROM settlement_eligibility_decisions WHERE decision_id=NEW.decision_id)
        BEGIN SELECT RAISE(ABORT, 'Eligibility replacement forbidden'); END""")
    for action in ('UPDATE','DELETE'):
        conn.execute("CREATE TRIGGER eligibility_artifact_no_"+action.lower()+" BEFORE "+action+
            " ON settlement_eligibility_decisions BEGIN SELECT RAISE(ABORT, 'Immutable eligibility approval'); END")
    # Approvals cannot become stale through subsequent dependency edits or replacements.
    dependencies = (
        ('work_deliveries','delivery_id','work_delivery_id'),
        ('raw_evidence','evidence_id','evidence_id'),
        ('validation_results','result_id','validation_result_id'),
        ('technical_decisions','decision_id','technical_decision_id'),
        ('contract_acceptance_decisions','decision_id','contract_acceptance_decision_id'))
    for table, key, reference in dependencies:
        for action in ('UPDATE','DELETE'):
            conn.execute(f"CREATE TRIGGER eligibility_freeze_{table}_{action.lower()} BEFORE {action} ON {table} "
                f"WHEN EXISTS(SELECT 1 FROM settlement_eligibility_decisions WHERE {reference}=OLD.{key}) "
                "BEGIN SELECT RAISE(ABORT, 'Referenced eligibility evidence is immutable'); END")
        conn.execute(f"CREATE TRIGGER eligibility_replace_{table} BEFORE INSERT ON {table} "
            f"WHEN EXISTS(SELECT 1 FROM settlement_eligibility_decisions WHERE {reference}=NEW.{key}) "
            "BEGIN SELECT RAISE(ABORT, 'Referenced eligibility evidence cannot be replaced'); END")
    for action in ('UPDATE','DELETE'):
        conn.execute(f"CREATE TRIGGER eligibility_freeze_external_event_{action.lower()} BEFORE {action} ON external_acceptance_events "
            "WHEN EXISTS(SELECT 1 FROM settlement_eligibility_decisions q JOIN contract_acceptance_decisions c "
            "ON c.decision_id=q.contract_acceptance_decision_id WHERE c.acceptance_event_id=OLD.event_id) "
            "BEGIN SELECT RAISE(ABORT, 'Referenced contractual evidence is immutable'); END")
    conn.execute("""CREATE TRIGGER eligibility_replace_external_event BEFORE INSERT ON external_acceptance_events
        WHEN EXISTS(SELECT 1 FROM settlement_eligibility_decisions q JOIN contract_acceptance_decisions c
            ON c.decision_id=q.contract_acceptance_decision_id WHERE c.acceptance_event_id=NEW.event_id)
        BEGIN SELECT RAISE(ABORT, 'Referenced contractual evidence cannot be replaced'); END""")
    # New SQL inserts cannot bypass eligibility even outside create_settlement().
    conn.execute("""CREATE TRIGGER eligibility_settlement_insert_guard BEFORE INSERT ON canonical_settlements
        WHEN NOT EXISTS(SELECT 1 FROM settlement_eligibility_decisions q
            WHERE q.decision_id=NEW.eligibility_decision_id AND q.approved=1
              AND q.task_id=NEW.task_id AND q.environment=NEW.environment
              AND q.contract_acceptance_decision_id=NEW.contract_acceptance_decision_id)
        BEGIN SELECT RAISE(ABORT, 'Approved matching eligibility required'); END""")
    conn.execute("""CREATE TRIGGER eligibility_settlement_no_replace BEFORE INSERT ON canonical_settlements
        WHEN EXISTS(SELECT 1 FROM canonical_settlements WHERE settlement_id=NEW.settlement_id
            OR obligation_id=NEW.obligation_id OR payment_authorization_id=NEW.payment_authorization_id)
        BEGIN SELECT RAISE(ABORT, 'Settlement replacement forbidden'); END""")
    conn.execute("""CREATE TRIGGER eligibility_settlement_binding_immutable
        BEFORE UPDATE OF eligibility_decision_id, task_id, environment, contract_acceptance_decision_id ON canonical_settlements
        WHEN NEW.eligibility_decision_id IS NOT OLD.eligibility_decision_id OR NEW.task_id IS NOT OLD.task_id
          OR NEW.environment IS NOT OLD.environment OR NEW.contract_acceptance_decision_id IS NOT OLD.contract_acceptance_decision_id
        BEGIN SELECT RAISE(ABORT, 'Immutable settlement eligibility binding'); END""")
    conn.execute("INSERT INTO schema_meta(version,migration_name,applied_at) VALUES (?,?,?)",
        (MIGRATION_VERSION,MIGRATION_NAME,datetime.now(timezone.utc).isoformat()))


def migrate():
    """Back up original database before opening it for any write, then apply atomically."""
    source_path = Path(db.DB_PATH).resolve()
    backup_dir = source_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    backup_path = backup_dir / ('pre-eligibility-008-' + stamp + '.db')
    with closing(sqlite3.connect(source_path.as_uri()+'?mode=ro',uri=True)) as source:
        with closing(sqlite3.connect(backup_path)) as backup:
            source.backup(backup)
            backup.commit()
            if backup.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise RuntimeError('Backup integrity check failed')
    with db.get_db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        try:
            apply_schema(conn)
            if conn.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or conn.execute('PRAGMA foreign_key_check').fetchall():
                raise RuntimeError('Migrated database integrity check failed')
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
    print('Migration 008 applied; no historical eligibility or provenance backfilled')
    print('Verified backup:', backup_path)

if __name__ == '__main__':
    migrate()
