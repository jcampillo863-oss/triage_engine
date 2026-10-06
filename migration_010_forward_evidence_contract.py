"""Forward evidence-write enforcement. Existing rows are never rewritten.

Use only through an explicitly reviewed upgrade transaction or fresh bootstrap.
SQLite RAISE(ABORT) is deliberate: OR IGNORE/REPLACE cannot override it.
"""
from datetime import datetime, timezone
import db

MIGRATION_VERSION = 10
MIGRATION_NAME = "forward_evidence_contract"

RAW_INVALID = """typeof(NEW.git_commit_hash) <> 'text'
 OR length(trim(NEW.git_commit_hash, char(9,10,11,12,13,28,29,30,31,32,133,160,5760,8192,8193,8194,8195,8196,8197,8198,8199,8200,8201,8202,8232,8233,8239,8287,12288))) = 0
 OR typeof(NEW.syntax_valid) <> 'integer' OR NEW.syntax_valid NOT IN (0,1)
 OR typeof(NEW.compilation_valid) <> 'integer' OR NEW.compilation_valid NOT IN (0,1)
 OR typeof(NEW.diff_policy_passed) <> 'integer' OR NEW.diff_policy_passed NOT IN (0,1)
 OR (NEW.pytest_exit_code IS NOT NULL AND typeof(NEW.pytest_exit_code) <> 'integer')"""

# An object of named check statuses; {} remains a valid compatibility observation.
# This is serialization enforcement, not a new technical acceptance policy.
CHECKS_INVALID = """CASE
 WHEN typeof(NEW.checks_json) <> 'text' THEN 1
 WHEN json_valid(NEW.checks_json) <> 1 THEN 1
 WHEN json_type(NEW.checks_json) <> 'object' THEN 1
 ELSE EXISTS (SELECT 1 FROM json_each(NEW.checks_json)
   WHERE type <> 'text' OR length(trim(key)) = 0
     OR value NOT IN ('PASSED','FAILED','ERROR','SKIPPED','INCONCLUSIVE')) END"""


def apply_schema(conn):
    row = conn.execute('SELECT migration_name FROM schema_meta WHERE version=10').fetchone()
    if row:
        if row[0] != MIGRATION_NAME:
            raise RuntimeError('Conflicting forward evidence migration metadata')
        return
    prerequisite = conn.execute('SELECT migration_name FROM schema_meta WHERE version=9').fetchone()
    if not prerequisite or prerequisite[0] != 'payment_provider_boundary':
        raise RuntimeError('Migration 009 prerequisite required')
    # No SELECT/UPDATE of historical observations, and no historical backfill.
    for table, invalid, identity in (
        ('raw_evidence', RAW_INVALID, 'evidence_id'),
        ('validation_results', CHECKS_INVALID, 'result_id'),
    ):
        for operation in ('INSERT', 'UPDATE'):
            conn.execute(f"CREATE TRIGGER forward_evidence_{table}_{operation.lower()} "
                f"BEFORE {operation} ON {table} WHEN {invalid} "
                "BEGIN SELECT RAISE(ABORT,'Invalid forward evidence observation'); END")
        # REPLACE implicitly deletes without delete triggers when recursive_triggers
        # is off. Block collisions before that deletion, regardless of conflict mode.
        conn.execute(f"CREATE TRIGGER forward_evidence_{table}_no_replace "
            f"BEFORE INSERT ON {table} WHEN EXISTS(SELECT 1 FROM {table} WHERE {identity}=NEW.{identity}) "
            "BEGIN SELECT RAISE(ABORT,'Evidence identity replacement forbidden'); END")
        conn.execute(f"CREATE TRIGGER forward_evidence_{table}_no_update_replace "
            f"BEFORE UPDATE ON {table} WHEN NEW.{identity} IS NOT OLD.{identity} "
            f"AND EXISTS(SELECT 1 FROM {table} WHERE {identity}=NEW.{identity}) "
            "BEGIN SELECT RAISE(ABORT,'Evidence update replacement forbidden'); END")
    conn.execute('INSERT INTO schema_meta VALUES(?,?,?)',
        (MIGRATION_VERSION, MIGRATION_NAME, datetime.now(timezone.utc).isoformat()))


def migrate():
    with db.get_db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        apply_schema(conn)
        conn.commit()


if __name__ == '__main__':
    raise SystemExit('Explicit reviewed upgrade required; no automatic live migration')
