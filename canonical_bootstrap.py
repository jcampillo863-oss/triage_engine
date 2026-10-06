"""Construct a fresh canonical DB, or read-only validate a current one.

Existing older/partial databases require a separately reviewed upgrade. No provider
or authority operations are invoked. schema.sql is deliberately not used.
"""
import argparse
from contextlib import contextmanager, redirect_stdout, closing
import importlib
import io
import os
from pathlib import Path
import sqlite3
import tempfile

import db

# Version 007 is intentionally a historically unregistered schema helper.
MIGRATIONS = (
    (1, 'migration_001_database_foundation', 'apply_migration', 'database_foundation'),
    (2, 'migration_002_canonical_marketplace', 'apply_migration', 'canonical_marketplace_foundation'),
    (3, 'migration_003_canonical_settlement_model', 'apply_migration', 'canonical_settlement_model'),
    (4, 'migration_004_work_delivery_provenance', 'migrate', 'work_delivery_provenance'),
    (5, 'migration_005_acceptance_provenance', 'migrate', 'acceptance_provenance_linkage'),
    (6, 'migration_006_payment_authorization_evidence', 'migrate', 'payment_authorization_evidence'),
    (7, 'migration_007_capture_attempt_guard', 'migrate', None),
    (8, 'migration_008_settlement_eligibility', 'migrate', 'canonical_settlement_eligibility'),
)


class BootstrapError(RuntimeError):
    pass


@contextmanager
def _database_path(path):
    # Existing components share this process-local DB binding. Never change env.
    previous = db.DB_DIR, db.DB_PATH
    db.DB_DIR, db.DB_PATH = str(path.parent), str(path)
    try:
        yield
    finally:
        db.DB_DIR, db.DB_PATH = previous


def _construct(path, after_step=None):
    with _database_path(path), redirect_stdout(io.StringIO()):
        db.init_db()
        if after_step:
            after_step(0, path)
        for version, module_name, entry, name in MIGRATIONS:
            module = importlib.import_module(module_name)
            if name is not None and (module.MIGRATION_VERSION != version or module.MIGRATION_NAME != name):
                raise BootstrapError('Migration registry conflicts with committed migration')
            getattr(module, entry)()
            if after_step:
                after_step(version, path)
    # All component connections are closed. Fold WAL into the main file before
    # moving its directory entry; refuse publication if checkpointing is busy.
    with closing(sqlite3.connect(path)) as conn:
        if conn.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()[0] != 0:
            raise BootstrapError('WAL checkpoint busy; publication refused')
        if conn.execute('PRAGMA journal_mode=DELETE').fetchone()[0].lower() != 'delete':
            raise BootstrapError('Could not close WAL publication state')
    for suffix in ('-wal', '-shm'):
        sidecar = Path(str(path) + suffix)
        if sidecar.exists() and sidecar.stat().st_size:
            raise BootstrapError('Uncheckpointed sidecar; publication refused')


def _readonly(path):
    return sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)


def _schema(conn):
    # Compare definitions, including CHECK constraints, unique indexes and all
    # enforcement triggers. Definitions are compared exactly against the schema
    # emitted by this fixed initializer/migration chain.
    return tuple((kind, name, table, sql.strip() if sql else None)
                 for kind, name, table, sql in conn.execute(
                     "SELECT type,name,tbl_name,sql FROM sqlite_master "
                     "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"))


def _validate(path, reference_schema, fresh=False):
    try:
        with closing(_readonly(path)) as conn:
            if conn.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
                raise BootstrapError('Database integrity check failed')
            if conn.execute('PRAGMA foreign_key_check').fetchall():
                raise BootstrapError('Database foreign-key check failed')
            expected = [(v, name) for v, _, _, name in MIGRATIONS if name is not None]
            actual = conn.execute('SELECT version,migration_name FROM schema_meta ORDER BY version').fetchall()
            if actual != expected:
                raise BootstrapError('Older, future or conflicting migration metadata; upgrade review required')
            if _schema(conn) != reference_schema:
                raise BootstrapError('Partial or conflicting canonical schema; upgrade review required')
            if fresh:
                tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name!='schema_meta'").fetchall()
                for (table,) in tables:
                    # Identifiers originate exclusively in committed schema.
                    quoted = '"' + table.replace('"', '""') + '"'
                    if conn.execute('SELECT COUNT(*) FROM ' + quoted).fetchone()[0]:
                        raise BootstrapError('Fresh database contains unexpected historical/economic rows')
    except sqlite3.Error as exc:
        raise BootstrapError('Unreadable or incomplete database; upgrade review required') from exc


def bootstrap(database=None, *, _after_step=None, _before_publish=None):
    """Fresh publication or current read-only no-op; hooks are isolated-test seams.

    Run in a dedicated process: existing db globals are temporarily rebound while
    constructing private databases. Existing target bytes are never migrated.
    """
    target = Path(database if database is not None else Path(__file__).resolve().parent / 'data' / 'settlement.db').resolve()
    # Reference is synthesized from committed components, never local DB state.
    with tempfile.TemporaryDirectory(prefix='canonical-schema-reference-') as reference_dir:
        reference = Path(reference_dir) / 'reference.db'
        _construct(reference)
        with closing(_readonly(reference)) as conn:
            reference_schema = _schema(conn)
        _validate(reference, reference_schema, fresh=True)
        if target.exists():
            _validate(target, reference_schema)
            return {'database': str(target), 'outcome': 'current_read_only',
                    'migration_007': 'structure verified; historically unregistered'}
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='.canonical-bootstrap-', dir=target.parent) as staged_dir:
            staged = Path(staged_dir) / 'settlement.db'
            _construct(staged, _after_step)
            _validate(staged, reference_schema, fresh=True)
            if _before_publish:
                _before_publish(target, staged)
            # Same-filesystem hard-link creation is atomic and fails if ANY target
            # already exists. Unlike replace/rename variants it cannot overwrite.
            # The private staged link is removed by TemporaryDirectory afterwards.
            try:
                with staged.open('r+b') as handle:
                    os.fsync(handle.fileno())
                os.link(staged, target)
            except FileExistsError as exc:
                raise BootstrapError('Target appeared concurrently; publication refused') from exc
            except OSError as exc:
                raise BootstrapError('Atomic no-overwrite publication unavailable; target not replaced') from exc
        return {'database': str(target), 'outcome': 'created',
                'migration_007': 'structure verified; historically unregistered'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, help='Target SQLite path (default: checkout/data/settlement.db)')
    args = parser.parse_args(argv)
    try:
        result = bootstrap(args.db)
    except (BootstrapError, OSError, sqlite3.Error) as exc:
        # No arbitrary migration/provider exception bodies or credential values.
        print('Canonical bootstrap failed: ' + (str(exc) if isinstance(exc, BootstrapError) else type(exc).__name__))
        return 1
    print('Canonical bootstrap: ' + result['outcome'])
    print('Database: ' + result['database'])
    print('Migration 007: ' + result['migration_007'])
    print('Integrity: ok; foreign-key violations: 0')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
