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

names = ['test_github_acceptance_auth', 'test_github_acceptance_semantics',
 'test_work_delivery', 'test_external_acceptance', 'test_acceptance_binding',
 'test_payment_authority', 'test_payment_authority_invariants',
 'test_canonical_settlement_engine', 'test_canonical_invariants',
 'test_canonical_revenue', 'test_revenue_recording', 'test_paypal_adapter',
 'test_paypal_capture_service',
 'test_paypal_reconciliation_adapter', 'test_paypal_reconciliation_observer']
if '--receiver' in sys.argv:
    names.append('test_github_receiver')
    names.append('test_capture_restart_safety')
def blocked(*args, **kwargs):
    raise RuntimeError('Network disabled during isolated canonical tests')
socket.create_connection = blocked
socket.socket.connect = blocked
with tempfile.TemporaryDirectory(prefix='canonical-sprint-', dir=root / 'data') as tmp:
    target = pathlib.Path(tmp) / 'settlement.db'
    source = sqlite3.connect(pathlib.Path(db.DB_PATH).as_uri() + '?mode=ro', uri=True)
    dest = sqlite3.connect(target)
    source.backup(dest)
    source.close()
    dest.close()
    db.DB_PATH = str(target)
    db.DB_DIR = tmp
    from migration_008_settlement_eligibility import apply_schema
    with db.get_db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        apply_schema(conn)
        conn.commit()
    names.append("test_settlement_eligibility")
    names.append("test_service_principals")
    names.append("test_paypal_canonical_integration")
    names.append("test_snapshot_source_hygiene")
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
    print('isolated_database_integrity:', conn.execute('PRAGMA integrity_check').fetchone()[0])
    print('isolated_foreign_key_errors:', len(conn.execute('PRAGMA foreign_key_check').fetchall()))
    conn.close()
    print('scripts:', len(names), 'failures:', len(failures))
    sys.exit(bool(failures))
