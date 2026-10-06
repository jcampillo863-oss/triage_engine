"""Canonical Bootstrap V1 tests: temporary databases only, no provider operations."""
import builtins
from contextlib import closing, redirect_stdout
import io
import os
from pathlib import Path
import runpy
import socket
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import canonical_bootstrap as bootstrap
import db

ROOT = Path(__file__).resolve().parent


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='canonical-bootstrap-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.target = self.root / 'missing data' / 'fresh.db'
        self.binding = db.DB_DIR, db.DB_PATH
        for attribute in ('create_connection', 'getaddrinfo'):
            blocker = patch.object(socket, attribute, side_effect=AssertionError('Network forbidden'))
            blocker.start()
            self.addCleanup(blocker.stop)
        blocker = patch.object(socket.socket, 'connect', side_effect=AssertionError('Network forbidden'))
        blocker.start()
        self.addCleanup(blocker.stop)
        blocker = patch.object(socket.socket, 'connect_ex', side_effect=AssertionError('Network forbidden'))
        blocker.start()
        self.addCleanup(blocker.stop)

    def tearDown(self):
        self.assertEqual((db.DB_DIR, db.DB_PATH), self.binding)

    def create(self):
        self.assertEqual(bootstrap.bootstrap(self.target)['outcome'], 'created')

    def test_fresh_missing_path_empty_and_valid(self):
        self.create()
        with closing(sqlite3.connect(self.target)) as conn:
            self.assertEqual(conn.execute('PRAGMA integrity_check').fetchall(), [('ok',)])
            self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(), [])
            self.assertEqual(conn.execute('SELECT version FROM schema_meta ORDER BY version').fetchall(),
                             [(1,), (2,), (3,), (4,), (5,), (6,), (8,), (9,), (10,)])
            for (table,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT GLOB 'sqlite_*' AND name!='schema_meta'").fetchall():
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM "' + table + '"').fetchone()[0], 0)

    def test_exact_substrate_and_migration_order(self):
        steps = []
        bootstrap.bootstrap(self.target, _after_step=lambda version, path: steps.append(version))
        self.assertEqual(steps, list(range(11)))
        self.assertEqual([entry[0] for entry in bootstrap.MIGRATIONS], list(range(1, 11)))
        self.assertIsNone(bootstrap.MIGRATIONS[6][3])

    def test_repeat_is_byte_preserving_no_backup(self):
        self.create()
        before = self.target.read_bytes()
        listing = sorted(str(p.relative_to(self.root)) for p in self.root.rglob('*'))
        result = bootstrap.bootstrap(self.target)
        self.assertEqual(result['outcome'], 'current_read_only')
        self.assertIn('historically unregistered', result['migration_007'])
        self.assertEqual(before, self.target.read_bytes())
        self.assertEqual(listing, sorted(str(p.relative_to(self.root)) for p in self.root.rglob('*')))

    def test_current_evidence_preserved(self):
        self.create()
        with closing(sqlite3.connect(self.target)) as conn:
            conn.execute("INSERT INTO raw_evidence VALUES ('synthetic-evidence','synthetic-task','synthetic-commit',0,1,1,1,'{}','test-time')")
            conn.commit()
            metadata = conn.execute('SELECT * FROM schema_meta').fetchall()
        before = self.target.read_bytes()
        bootstrap.bootstrap(self.target)
        self.assertEqual(before, self.target.read_bytes())
        with closing(sqlite3.connect(self.target)) as conn:
            self.assertEqual(conn.execute('SELECT * FROM schema_meta').fetchall(), metadata)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM raw_evidence').fetchone()[0], 1)

    def test_failure_at_each_committed_step_never_publishes(self):
        for failed_step in range(11):
            with self.subTest(step=failed_step):
                def fail(version, path):
                    if version == failed_step:
                        raise RuntimeError('Injected failure')
                with self.assertRaises(RuntimeError):
                    bootstrap.bootstrap(self.target, _after_step=fail)
                self.assertFalse(self.target.exists())
                self.assertEqual(list(self.target.parent.glob('.canonical-bootstrap-*')), [])

    def test_partial_database_rejected_unchanged(self):
        self.target.parent.mkdir()
        with closing(sqlite3.connect(self.target)) as conn:
            conn.execute('CREATE TABLE partial(x)')
            conn.commit()
        before = self.target.read_bytes()
        with self.assertRaises(bootstrap.BootstrapError):
            bootstrap.bootstrap(self.target)
        self.assertEqual(before, self.target.read_bytes())

    def corrupt_metadata(self, sql):
        self.create()
        with closing(sqlite3.connect(self.target)) as conn:
            conn.execute(sql)
            conn.commit()
        before = self.target.read_bytes()
        with self.assertRaises(bootstrap.BootstrapError):
            bootstrap.bootstrap(self.target)
        self.assertEqual(before, self.target.read_bytes())

    def test_future_version_rejected(self):
        self.corrupt_metadata("INSERT INTO schema_meta VALUES(11,'future','now')")

    def test_older_version_rejected(self):
        self.corrupt_metadata('DELETE FROM schema_meta WHERE version=8')

    def test_conflicting_name_rejected(self):
        self.corrupt_metadata("UPDATE schema_meta SET migration_name='conflict' WHERE version=2")

    def test_fabricated_version_seven_rejected(self):
        self.corrupt_metadata("INSERT INTO schema_meta VALUES(7,'invented','now')")

    def test_missing_guard_rejected(self):
        self.create()
        with closing(sqlite3.connect(self.target)) as conn:
            conn.execute('DROP TRIGGER eligibility_settlement_insert_guard')
            conn.commit()
        with self.assertRaises(bootstrap.BootstrapError):
            bootstrap.bootstrap(self.target)

    def test_additional_schema_requires_review(self):
        self.create()
        with closing(sqlite3.connect(self.target)) as conn:
            conn.execute('CREATE TABLE unexpected(x)')
            conn.commit()
        with self.assertRaises(bootstrap.BootstrapError):
            bootstrap.bootstrap(self.target)

    def test_foreign_key_violation_rejected(self):
        self.create()
        with closing(sqlite3.connect(self.target)) as conn:
            conn.execute("INSERT INTO validation_results(validator_id,evidence_id,status,checks_json,created_at) VALUES('synthetic-validator','absent','PASS','{}','now')")
            conn.commit()
        with self.assertRaisesRegex(bootstrap.BootstrapError, 'foreign-key'):
            bootstrap.bootstrap(self.target)

    def test_concurrent_target_not_overwritten(self):
        def concurrent(target, staged):
            target.write_bytes(b'concurrently-created-target')
        with self.assertRaisesRegex(bootstrap.BootstrapError, 'concurrently'):
            bootstrap.bootstrap(self.target, _before_publish=concurrent)
        self.assertEqual(self.target.read_bytes(), b'concurrently-created-target')

    def test_unsupported_publication_fails_closed(self):
        with patch.object(bootstrap.os, 'link', side_effect=OSError('unsupported')):
            with self.assertRaisesRegex(bootstrap.BootstrapError, 'unavailable'):
                bootstrap.bootstrap(self.target)
        self.assertFalse(self.target.exists())

    def test_wal_checkpoint_preserves_schema_and_metadata(self):
        observations = []
        def observe(version, path):
            with closing(sqlite3.connect(path)) as conn:
                observations.append(conn.execute('PRAGMA journal_mode').fetchone()[0])
        bootstrap.bootstrap(self.target, _after_step=observe)
        self.assertEqual(observations, ['wal'] * 11)
        self.assertFalse(Path(str(self.target) + '-wal').exists())
        with closing(sqlite3.connect(self.target)) as conn:
            self.assertEqual(conn.execute('PRAGMA journal_mode').fetchone()[0], 'delete')
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM schema_meta').fetchone()[0], 9)
            self.assertIsNotNone(conn.execute("SELECT 1 FROM sqlite_master WHERE name='eligibility_settlement_insert_guard'").fetchone())

    def test_paths_spaces_different_cwd_cli(self):
        result = subprocess.run([sys.executable, '-B', str(ROOT / 'canonical_bootstrap.py'), '--db', str(self.target)],
                                cwd=self.root, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(self.target.exists())

    def test_no_schema_sql_credentials_provider_or_finance(self):
        import paypal_capture_service
        import requests
        real_open = builtins.open
        def guarded_open(file, *args, **kwargs):
            if str(file).endswith('schema.sql'):
                raise AssertionError('Legacy SQL forbidden')
            return real_open(file, *args, **kwargs)
        with patch.dict(os.environ, {}, clear=True), patch('builtins.open', side_effect=guarded_open), \
             patch.object(requests.sessions.Session, 'request', side_effect=AssertionError('Provider forbidden')) as provider, \
             patch.object(paypal_capture_service, 'prepare_capture', side_effect=AssertionError('Finance forbidden')) as prepare, \
             patch.object(paypal_capture_service, 'execute_capture', side_effect=AssertionError('Finance forbidden')) as capture:
            self.create()
            provider.assert_not_called()
            prepare.assert_not_called()
            capture.assert_not_called()

    def test_verifier_missing_database_diagnostic(self):
        result = subprocess.run([sys.executable, '-B', str(ROOT / 'verify_production_candidate.py'), '--db', str(self.target)],
                                cwd=self.root, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('canonical_bootstrap.py', result.stdout)
        self.assertFalse(self.target.exists())

    def test_verifier_after_bootstrap_full_approved_suite(self):
        self.create()
        before = self.target.read_bytes()
        result = subprocess.run([sys.executable, '-B', str(ROOT / 'verify_production_candidate.py'), '--db', str(self.target), '--receiver'],
                                cwd=self.root, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('scripts: 23 failures: 0', result.stdout)
        self.assertIn('isolated_database_integrity: ok', result.stdout)
        self.assertEqual(before, self.target.read_bytes())

    def test_failed_structural_validation_never_publishes(self):
        def damage(version, path):
            if version == 8:
                with closing(sqlite3.connect(path)) as conn:
                    conn.execute('DROP TRIGGER eligibility_settlement_insert_guard')
                    conn.commit()
        with self.assertRaisesRegex(bootstrap.BootstrapError, 'schema'):
            bootstrap.bootstrap(self.target, _after_step=damage)
        self.assertFalse(self.target.exists())

    def test_current_claim_and_economic_rows_preserved(self):
        from eligibility_test_support import seed_chain, seed_authorization
        from settlement_eligibility import approve_settlement_eligibility
        from canonical_settlement_engine import create_settlement
        self.create()
        with closing(sqlite3.connect(self.target)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute('PRAGMA foreign_keys=ON')
            chain = seed_chain(conn)
            authorization = seed_authorization(conn, chain)
            decision = approve_settlement_eligibility(conn, **chain)
            settlement = create_settlement(conn, task_id=chain['task_id'],
                obligation_id='synthetic-bootstrap-obligation',
                contract_acceptance_decision_id=chain['contract_acceptance_decision_id'],
                payment_authorization_id=authorization, amount_cents=100, currency='AUD',
                provider='TEST_PROVIDER', environment='TEST', eligibility_decision_id=decision['decision_id'])
            conn.execute('INSERT INTO canonical_capture_attempts(settlement_id,request_id) VALUES (?,?)',
                         (settlement['settlement_id'], 'synthetic-bootstrap-claim'))
            conn.commit()
            rows = conn.execute('SELECT * FROM canonical_capture_attempts').fetchall()
            rows = [tuple(row) for row in rows]
        before = self.target.read_bytes()
        bootstrap.bootstrap(self.target)
        self.assertEqual(before, self.target.read_bytes())
        with closing(sqlite3.connect(self.target)) as conn:
            self.assertEqual(conn.execute('SELECT * FROM canonical_capture_attempts').fetchall(), rows)

    def test_corrupt_database_rejected_without_changes(self):
        self.target.parent.mkdir()
        self.target.write_bytes(b'not a SQLite database')
        with self.assertRaises(bootstrap.BootstrapError):
            bootstrap.bootstrap(self.target)
        self.assertEqual(self.target.read_bytes(), b'not a SQLite database')

    def test_verifier_from_source_copy_without_data_directory(self):
        import shutil
        source_copy = self.root / 'sterile source'
        source_copy.mkdir()
        # Only source/reproducibility assets: no databases, env or runtime state.
        for path in ROOT.glob('*.py'):
            shutil.copyfile(path, source_copy / path.name)
        fixture_dir = source_copy / 'workspace' / 'task_heavy_task_1500'
        fixture_dir.mkdir(parents=True)
        for name in ('sfloadmacro.py', 'test_sfloadmacro.py'):
            shutil.copyfile(ROOT / 'workspace' / 'task_heavy_task_1500' / name, fixture_dir / name)
        self.create()
        before = self.target.read_bytes()
        result = subprocess.run([sys.executable, '-B', str(source_copy / 'verify_production_candidate.py'),
                                 '--db', str(self.target), '--receiver'],
                                cwd=self.root, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('scripts: 23 failures: 0', result.stdout)
        self.assertFalse((source_copy / 'data').exists())
        self.assertEqual(before, self.target.read_bytes())

    def test_verifier_final_fk_check_fails_even_when_scripts_pass(self):
        self.create()
        with closing(sqlite3.connect(self.target)) as conn:
            conn.execute("INSERT INTO validation_results(validator_id,evidence_id,status,checks_json,created_at) VALUES('synthetic-validator','absent','PASS','{}','now')")
            conn.commit()
        original_run = runpy.run_path
        output = io.StringIO()
        # Isolate the verifier's exit behavior from individual fixture scripts.
        # Its input remains a temporary database; nested scripts are stubbed.
        with patch.object(sys, 'argv', ['verify_production_candidate.py', '--db', str(self.target)]), \
             patch.object(runpy, 'run_path', return_value={}), patch.object(bootstrap, 'validate_database', return_value=None), redirect_stdout(output), \
             patch.object(socket, 'create_connection'), patch.object(socket.socket, 'connect'), \
             patch.object(socket.socket, 'connect_ex'), patch.object(socket, 'getaddrinfo'):
            try:
                with self.assertRaises(SystemExit) as raised:
                    original_run(str(ROOT / 'verify_production_candidate.py'), run_name='__main__')
                self.assertEqual(raised.exception.code, 1)
            finally:
                db.DB_DIR, db.DB_PATH = self.binding
        self.assertIn('isolated_foreign_key_errors: 1', output.getvalue())


if __name__ == '__main__':
    unittest.main()
