import argparse
import contextlib
import io
import pathlib
import runpy
import socket
import sqlite3
import sys
import tempfile

root = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(root))
import db

parser = argparse.ArgumentParser(description='Isolated network-blocked canonical verification')
parser.add_argument('--db', type=pathlib.Path, default=pathlib.Path(db.DB_PATH),
                    help='Existing canonical database; verification uses only a copy')
parser.add_argument('--receiver', action='store_true')
parser.add_argument('--migration-compatibility', action='store_true',
                    help='Apply forward schemas only to a private copy; not actual-DB readiness')
parser.add_argument('--schema-only', action='store_true',
                    help='Validate supplied schema/integrity/FKs read-only without regression scripts')
args = parser.parse_args()
source_path = args.db.resolve()
if not source_path.is_file():
    print('Canonical database missing. Run canonical_bootstrap.py --db <database-path> first.')
    sys.exit(1)

names = ['test_github_acceptance_auth', 'test_github_acceptance_semantics',
 'test_work_delivery', 'test_external_acceptance', 'test_acceptance_binding',
 'test_payment_authority', 'test_payment_authority_invariants',
 'test_canonical_settlement_engine', 'test_canonical_invariants',
 'test_canonical_revenue', 'test_revenue_recording', 'test_paypal_adapter',
 'test_paypal_capture_service',
 'test_paypal_reconciliation_adapter', 'test_paypal_reconciliation_observer']
if args.receiver:
    names.append('test_github_receiver')
    names.append('test_capture_restart_safety')
def blocked(*args, **kwargs):
    raise RuntimeError('Network disabled during isolated canonical tests')
socket.create_connection = blocked
socket.socket.connect = blocked
socket.socket.connect_ex = blocked
socket.getaddrinfo = blocked
@contextlib.contextmanager
def isolated_fixture_directories(base):
    # Existing approved fixture scripts request checkout/data. Redirect only
    # that location into this verification run; do not require/create local data.
    original = tempfile.TemporaryDirectory
    def temporary_directory(*positional, **keywords):
        requested = keywords.get('dir')
        if requested is not None and pathlib.Path(requested).resolve() == root / 'data':
            keywords['dir'] = base
        return original(*positional, **keywords)
    tempfile.TemporaryDirectory = temporary_directory
    try:
        yield
    finally:
        tempfile.TemporaryDirectory = original

with tempfile.TemporaryDirectory(prefix='canonical-sprint-') as tmp, isolated_fixture_directories(tmp):
    target = pathlib.Path(tmp) / 'settlement.db'
    source = sqlite3.connect(source_path.as_uri() + '?mode=ro', uri=True)
    dest = sqlite3.connect(target)
    source.backup(dest)
    source.close()
    dest.close()
    db.DB_PATH = str(target)
    db.DB_DIR = tmp
    import canonical_bootstrap
    if args.migration_compatibility:
        from migration_008_settlement_eligibility import apply_schema
        from migration_009_payment_provider_boundary import apply_schema as apply_payment_schema
        from migration_010_forward_evidence_contract import apply_schema as apply_evidence_schema
        with db.get_db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            apply_schema(conn)
            apply_payment_schema(conn)
            apply_evidence_schema(conn)
            conn.commit()
        print('mode: migration_compatibility_only; supplied_database_readiness: NOT_ESTABLISHED')
    else:
        print('mode: supplied_database_readiness')
    try:
        # target is an unchanged SQLite backup in readiness mode, not an upgraded fixture.
        if args.migration_compatibility:
            canonical_bootstrap.validate_migration_compatibility(target)
        else:
            canonical_bootstrap.validate_database(target)
    except (canonical_bootstrap.BootstrapError, sqlite3.Error, OSError):
        print('supplied_database_schema: FAILED; required structure/integrity/FKs unavailable')
        sys.exit(1)
    if args.migration_compatibility:
        print('isolated_forward_payment_schema: valid; historical_base_readiness: NOT_ESTABLISHED')
    else:
        print('supplied_database_schema: current_structurally_valid')
    if args.schema_only:
        sys.exit(0)
    names.append("test_settlement_eligibility")
    names.append("test_service_principals")
    names.append("test_paypal_canonical_integration")
    names.append("test_snapshot_source_hygiene")
    names.append("test_production_payment_boundary")
    names.append("test_forward_evidence_schema")
    failures = []
    for name in names:
        output = io.StringIO()
        try:
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                runpy.run_path(str(root / (name + '.py')), run_name='__main__')
            if '[FAIL]' in output.getvalue() or ': FAILED' in output.getvalue():
                raise AssertionError('Script reported failure')
            print(name + ': PASS')
        except BaseException as exc:
            if isinstance(exc, SystemExit) and exc.code in (None, 0):
                print(name + ': PASS')
                for line in output.getvalue().splitlines():
                    if line.startswith('Ran '):
                        print(line)
                continue
            failures.append(name)
            print(name + ': FAIL (' + type(exc).__name__ + ')')
            # Print only test failure labels, never arbitrary exception/provider bodies.
            for line in output.getvalue().splitlines():
                if '[FAIL]' in line:
                    print(line.split(':')[0])
    conn = sqlite3.connect(target)
    integrity = conn.execute('PRAGMA integrity_check').fetchall()
    foreign_key_errors = len(conn.execute('PRAGMA foreign_key_check').fetchall())
    print('isolated_database_integrity:', 'ok' if integrity == [('ok',)] else 'FAILED')
    print('isolated_foreign_key_errors:', foreign_key_errors)
    if integrity != [('ok',)] or foreign_key_errors:
        failures.append('isolated_database_checks')
    conn.close()
    print('scripts:', len(names), 'failures:', len(failures))
    sys.exit(bool(failures))
