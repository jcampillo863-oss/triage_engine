"""Forward evidence contract: isolated databases and blocked network only."""
from contextlib import closing, redirect_stdout
import importlib
import io
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import canonical_bootstrap as bootstrap
from migration_009_payment_provider_boundary import apply_schema as upgrade9
from migration_010_forward_evidence_contract import apply_schema as upgrade10
from schema_readiness import HISTORICAL_BASE_TABLES, LEGACY_TABLES


class ForwardEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='forward-evidence-reference-')
        cls.fresh = Path(cls.temp.name)/'fresh.db'
        cls.historical = Path(cls.temp.name)/'historical.db'
        with patch.object(socket.socket,'connect',side_effect=AssertionError('Network forbidden')):
            bootstrap._construct(cls.fresh)
            with closing(sqlite3.connect(cls.historical)) as c:
                for sql in HISTORICAL_BASE_TABLES.values(): c.execute(sql)
                for sql in LEGACY_TABLES.values(): c.execute(sql)
                c.commit()
            with bootstrap._database_path(cls.historical), redirect_stdout(io.StringIO()):
                import db
                db.init_db()
                for version,module,entry,name in bootstrap.MIGRATIONS:
                    if version <= 8: getattr(importlib.import_module(module),entry)()
            with closing(sqlite3.connect(cls.historical)) as c:
                c.execute("INSERT INTO raw_evidence VALUES('historical','task','',NULL,NULL,NULL,NULL,'{}','past')")
                c.execute("INSERT INTO validation_results(validator_id,evidence_id,status,checks_json,created_at) VALUES('old','historical','INCONCLUSIVE',NULL,'past')")
                c.commit()
    @classmethod
    def tearDownClass(cls): cls.temp.cleanup()
    def setUp(self):
        self.temp_case = tempfile.TemporaryDirectory(prefix='forward-evidence-case-')
        self.addCleanup(self.temp_case.cleanup)
        self.root = Path(self.temp_case.name)
        for attr in ('connect','connect_ex'):
            p=patch.object(socket.socket,attr,side_effect=AssertionError('Network forbidden'))
            p.start();self.addCleanup(p.stop)
    def copy(self, source, name='case.db'):
        path=self.root/name
        with closing(sqlite3.connect(source)) as src, closing(sqlite3.connect(path)) as dest: src.backup(dest)
        return path
    def upgrade(self,path):
        with closing(sqlite3.connect(path)) as c:
            c.execute('PRAGMA foreign_keys=ON'); c.execute('BEGIN IMMEDIATE')
            upgrade9(c);upgrade10(c);c.commit()
    def snapshot(self,path):
        with closing(sqlite3.connect(path)) as c:
            return {n:c.execute('SELECT * FROM "'+n+'" ORDER BY rowid').fetchall()
                    for (n,) in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT GLOB 'sqlite_*' AND name!='schema_meta'").fetchall()}
    def forms(self):
        fresh=self.copy(self.fresh,'fresh.db');old=self.copy(self.historical,'old.db');self.upgrade(old)
        return fresh,old
    def insert_raw(self,c,identity='new',commit='abc',flags=(1,1,1),mode=''):
        c.execute('INSERT '+mode+' INTO raw_evidence VALUES(?,?,?,0,?,?,?,NULL,?)',
                  (identity,'task',commit,*flags,'now'))
    def insert_result(self,c,checks='{"syntax":"PASSED"}',mode='',result_id=None):
        c.execute('INSERT '+mode+' INTO validation_results(result_id,validator_id,evidence_id,status,checks_json,created_at) VALUES(?,?,?, ?,?,?)',
                  (result_id,'validator','new','PASSED',checks,'now'))
    def test_historical_008_rows_survive_009_010_without_backfill(self):
        p=self.copy(self.historical);before=self.snapshot(p);self.upgrade(p);after=self.snapshot(p)
        for name,rows in before.items(): self.assertEqual(after[name],rows,name)
        with closing(sqlite3.connect(p)) as c:
            self.assertEqual(c.execute('SELECT git_commit_hash,syntax_valid FROM raw_evidence').fetchone(),('',None))
            self.assertIsNone(c.execute('SELECT checks_json FROM validation_results').fetchone()[0])
            self.assertEqual(c.execute('PRAGMA integrity_check').fetchone(),('ok',))
            self.assertEqual(c.execute('PRAGMA foreign_key_check').fetchall(),[])
    def test_null_blank_commit_rejected_in_both_forms(self):
        for p in self.forms():
            with closing(sqlite3.connect(p)) as c:
                for v in (None,'',' ','\t\r\n','\u00a0','\u2003'):
                    with self.subTest(form=p.name,commit=v),self.assertRaises(sqlite3.IntegrityError): self.insert_raw(c,commit=v)
    def test_null_invalid_observations_rejected_in_both_forms(self):
        for p in self.forms():
            with closing(sqlite3.connect(p)) as c:
                for i in range(3):
                    for value in (None,-1,2,'true',0.5):
                        flags=[1,1,1];flags[i]=value
                        with self.subTest(form=p.name,column=i,value=value),self.assertRaises(sqlite3.IntegrityError): self.insert_raw(c,flags=flags)
    def test_null_invalid_checks_rejected_in_both_forms(self):
        for p in self.forms():
            with closing(sqlite3.connect(p)) as c:
                self.insert_raw(c)
                for checks in (None,'','not-json','null','[]','1','{"syntax":true}','{"syntax":"PASS"}','{"":"PASSED"}'):
                    with self.subTest(form=p.name,checks=checks),self.assertRaises(sqlite3.IntegrityError): self.insert_result(c,checks)
    def test_valid_forward_behavior_identical(self):
        for p in self.forms():
            with closing(sqlite3.connect(p)) as c:
                self.insert_raw(c);self.insert_result(c);c.commit()
                self.assertEqual(c.execute("SELECT syntax_valid,compilation_valid,diff_policy_passed FROM raw_evidence WHERE evidence_id='new'").fetchone(),(1,1,1))
                self.assertEqual(c.execute("SELECT status FROM validation_results WHERE evidence_id='new'").fetchone(),('PASSED',))
    def test_authenticated_work_operates_on_both_forward_forms(self):
        from authenticated_work import record_work_evidence, validate_work
        from work_delivery import create_delivery
        import service_principals as principals
        for p in self.forms():
            # Explicit test-only principal fixtures; no protected configuration.
            import base64
            producer=base64.urlsafe_b64encode(bytes(range(32))).decode().rstrip('=')
            validator=base64.urlsafe_b64encode(bytes(range(32,64))).decode().rstrip('=')
            with bootstrap._database_path(p), patch.dict(os.environ,{
                    principals.PRODUCER_SECRET_ENV:producer,
                    principals.VALIDATOR_SECRET_ENV:validator},clear=True):
                delivery=create_delivery(task_id='synthetic-auth-task',repository='example-owner/example-repo',
                    pull_request_number=123,pull_request_url='https://github.com/example-owner/example-repo/pull/123',
                    environment='PRODUCTION',dispatched_commit_hash='a'*40,producer_credential=producer)
                evidence=record_work_evidence(producer_credential=producer,work_delivery_id=delivery['delivery_id'],
                    evidence_id='synthetic-auth-evidence',pytest_exit_code=0,syntax_valid=True,
                    compilation_valid=True,diff_policy_passed=True,raw_payload={'synthetic':True})
                decision=validate_work(validator_credential=validator,work_delivery_id=delivery['delivery_id'],
                    evidence_id=evidence['evidence_id'])
                self.assertTrue(decision['accepted'])
                with closing(sqlite3.connect(p)) as c:
                    result=c.execute('SELECT status,checks_json FROM validation_results WHERE result_id=?',
                        (decision['validation_result_id'],)).fetchone()
                    self.assertEqual(result[0],'PASSED')
                    self.assertEqual(set(json.loads(result[1])),{'syntax','compilation','diff_policy','pytest'})
    def test_conflict_modes_cannot_ignore_invalid_inserts(self):
        for p in self.forms():
            with closing(sqlite3.connect(p)) as c:
                for mode in ('OR IGNORE','OR REPLACE','OR FAIL','OR ABORT','OR ROLLBACK'):
                    with self.assertRaises(sqlite3.IntegrityError):self.insert_raw(c,commit=None,mode=mode)
                self.insert_raw(c);c.commit()
                for mode in ('OR IGNORE','OR REPLACE','OR FAIL','OR ABORT','OR ROLLBACK'):
                    with self.assertRaises(sqlite3.IntegrityError):self.insert_result(c,checks=None,mode=mode)
    def test_replacements_cannot_delete_historical_or_current_identity(self):
        for p in self.forms():
            with closing(sqlite3.connect(p)) as c:
                c.execute('PRAGMA recursive_triggers=OFF');self.insert_raw(c);self.insert_result(c,result_id=50);c.commit()
                before=c.execute('SELECT * FROM raw_evidence').fetchall()
                for mode in ('OR REPLACE','OR IGNORE'):
                    with self.assertRaises(sqlite3.IntegrityError):self.insert_raw(c,mode=mode)
                    with self.assertRaises(sqlite3.IntegrityError):self.insert_result(c,mode=mode,result_id=50)
                self.assertEqual(c.execute('SELECT * FROM raw_evidence').fetchall(),before)
                if p.name=='old.db':
                    with self.assertRaises(sqlite3.IntegrityError):self.insert_raw(c,identity='historical',mode='OR REPLACE')
    def test_update_replace_cannot_delete_another_identity(self):
        for p in self.forms():
            with closing(sqlite3.connect(p)) as c:
                self.insert_raw(c);self.insert_raw(c,identity='other')
                self.insert_result(c,result_id=40);self.insert_result(c,result_id=50);c.commit()
                before=c.execute('SELECT * FROM raw_evidence').fetchall()
                with self.assertRaises(sqlite3.IntegrityError):
                    c.execute("UPDATE OR REPLACE raw_evidence SET evidence_id='new' WHERE evidence_id='other'")
                with self.assertRaises(sqlite3.IntegrityError):
                    c.execute('UPDATE OR REPLACE validation_results SET result_id=50 WHERE result_id=40')
                self.assertEqual(c.execute('SELECT * FROM raw_evidence').fetchall(),before)
    def test_update_and_upsert_cannot_introduce_invalid_values(self):
        for p in self.forms():
            with closing(sqlite3.connect(p)) as c:
                self.insert_raw(c);self.insert_result(c);c.commit()
                for sql in ("UPDATE OR IGNORE raw_evidence SET syntax_valid=NULL WHERE evidence_id='new'",
                            "UPDATE OR REPLACE raw_evidence SET git_commit_hash='' WHERE evidence_id='new'",
                            "UPDATE OR IGNORE validation_results SET checks_json=NULL WHERE evidence_id='new'",
                            "INSERT INTO raw_evidence VALUES('new','task','abc',0,1,1,1,NULL,'now') ON CONFLICT(evidence_id) DO UPDATE SET syntax_valid=NULL"):
                    with self.assertRaises(sqlite3.IntegrityError):c.execute(sql)
    def test_readiness_accepts_only_documented_historical_variance(self):
        p=self.copy(self.historical);self.upgrade(p);before=p.read_bytes()
        self.assertEqual(bootstrap.validate_database(p)['outcome'],'current_structurally_valid')
        self.assertEqual(before,p.read_bytes())
        with closing(sqlite3.connect(p)) as c:c.execute('CREATE TABLE unknown_legacy(x)');c.commit()
        with self.assertRaises(bootstrap.BootstrapError):bootstrap.validate_database(p)
    def test_formatting_only_variance_accepted(self):
        from schema_readiness import compatible_schema
        with closing(sqlite3.connect(self.fresh)) as c:ref=bootstrap._schema(c)
        formatted=[(kind,name,table,sql.replace('FOREIGN KEY(evidence_id)', 'FOREIGN KEY ( evidence_id )') if sql else sql) for kind,name,table,sql in ref]
        self.assertTrue(compatible_schema(formatted,ref))
    def test_missing_or_changed_authority_guard_fails_readiness(self):
        for guard in ('forward_evidence_raw_evidence_insert','forward_evidence_validation_results_update',
                      'forward_evidence_raw_evidence_no_replace','production_capture_confirmation_guard','eligibility_settlement_insert_guard'):
            p=self.copy(self.fresh,guard+'.db')
            with closing(sqlite3.connect(p)) as c:c.execute('DROP TRIGGER '+guard);c.commit()
            with self.assertRaises(bootstrap.BootstrapError):bootstrap.validate_database(p)
    def test_metadata_without_010_guards_fails(self):
        p=self.copy(self.historical);self.upgrade(p)
        with closing(sqlite3.connect(p)) as c:
            for (name,) in c.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'forward_evidence_%'").fetchall():c.execute('DROP TRIGGER '+name)
            c.commit()
        with self.assertRaises(bootstrap.BootstrapError):bootstrap.validate_database(p)
    def test_damaged_009_schema_still_fails_verifier(self):
        p=self.copy(self.fresh)
        with closing(sqlite3.connect(p)) as c:c.execute('DROP TRIGGER production_capture_confirmation_guard');c.commit()
        result=subprocess.run([sys.executable,'-B',str(Path(__file__).parent/'verify_production_candidate.py'),'--db',str(p),'--schema-only'],capture_output=True,text=True)
        self.assertNotEqual(result.returncode,0)
        self.assertIn('supplied_database_schema: FAILED',result.stdout)
    def test_intact_historical_schema_passes_verifier(self):
        p=self.copy(self.historical);self.upgrade(p)
        result=subprocess.run([sys.executable,'-B',str(Path(__file__).parent/'verify_production_candidate.py'),'--db',str(p),'--schema-only'],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stdout)
    def test_inventory_includes_application_names_and_excludes_sqlite_managed_objects(self):
        with closing(sqlite3.connect(':memory:')) as c:
            c.execute('CREATE TABLE ordinary_fixture(id INTEGER PRIMARY KEY AUTOINCREMENT, value TEXT UNIQUE)')
            c.execute('CREATE TABLE sqliteAppFixture(value TEXT)')
            c.execute('CREATE TABLE SQLiteAppMixedCase(value TEXT)')
            c.execute('CREATE INDEX sqliteAppIndex ON ordinary_fixture(value)')
            c.execute('CREATE TRIGGER sqliteAppTrigger AFTER INSERT ON ordinary_fixture BEGIN SELECT 1; END')
            full={row[0] for row in c.execute('SELECT name FROM sqlite_master')}
            inspected={row[1] for row in bootstrap._schema(c)}
            application={'ordinary_fixture','sqliteAppFixture','SQLiteAppMixedCase','sqliteAppIndex','sqliteAppTrigger'}
            self.assertTrue(application <= inspected)
            internal={name for name in full if name.startswith('sqlite_')}
            self.assertIn('sqlite_sequence',internal)
            self.assertTrue(any(name.startswith('sqlite_autoindex_') for name in internal))
            self.assertTrue(internal.isdisjoint(inspected))
    def test_readiness_rejects_undocumented_lookalike_table_index_and_trigger(self):
        statements=(
            'CREATE TABLE sqliteAppUndocumented(value TEXT)',
            'CREATE INDEX sqliteAppUndocumented ON schema_meta(migration_name)',
            'CREATE TRIGGER sqliteAppUndocumented AFTER INSERT ON schema_meta BEGIN SELECT 1; END',
        )
        for i,statement in enumerate(statements):
            p=self.copy(self.fresh,str(i)+'.db')
            with closing(sqlite3.connect(p)) as c:c.execute(statement);c.commit()
            with self.assertRaises(bootstrap.BootstrapError):bootstrap.validate_database(p)
    def test_fresh_inspection_detects_rows_in_application_lookalike_table(self):
        p=self.copy(self.fresh)
        with closing(sqlite3.connect(p)) as c:
            c.execute('CREATE TABLE sqliteAppFixture(value TEXT)')
            c.execute("INSERT INTO sqliteAppFixture VALUES('synthetic')");c.commit()
            reference=bootstrap._schema(c)
        # A test-only matching reference isolates the empty-row check from the
        # earlier rejection of undocumented objects. No authority is altered.
        with self.assertRaisesRegex(bootstrap.BootstrapError,'unexpected historical/economic rows'):
            bootstrap._validate(p,reference,fresh=True)
    def test_fresh_reference_inventory_cannot_omit_lookalike_application_objects(self):
        p=self.copy(self.fresh)
        with closing(sqlite3.connect(p)) as c:
            c.execute('CREATE TABLE sqliteAppFixture(value TEXT)')
            c.execute('CREATE INDEX sqliteAppIndex ON sqliteAppFixture(value)')
            c.execute('CREATE TRIGGER sqliteAppTrigger AFTER INSERT ON sqliteAppFixture BEGIN SELECT 1; END')
            c.commit();names={row[1] for row in bootstrap._schema(c)}
        self.assertTrue({'sqliteAppFixture','sqliteAppIndex','sqliteAppTrigger'} <= names)
    def test_migration_is_idempotent_no_row_changes(self):
        p=self.copy(self.historical);self.upgrade(p);before=p.read_bytes()
        with closing(sqlite3.connect(p)) as c:upgrade10(c);c.commit()
        self.assertEqual(before,p.read_bytes())


if __name__=='__main__':unittest.main(argv=['forward-evidence-schema'],exit=True)
