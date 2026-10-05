"""Eligibility regression coverage: isolated databases only, no external calls."""
from contextlib import contextmanager
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import uuid
import db
from migration_008_settlement_eligibility import apply_schema
from settlement_eligibility import approve_settlement_eligibility, SettlementEligibilityError
from eligibility_test_support import seed_chain, seed_authorization
from canonical_settlement_engine import create_settlement, get_settlement
from work_delivery import create_delivery, WorkDeliveryError

class EligibilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='eligibility-tests-',dir=Path(__file__).parent/'data')
        self.path=Path(self.tmp.name)/'test.db'
        source=sqlite3.connect(Path(db.DB_PATH).resolve().as_uri()+'?mode=ro',uri=True)
        self.conn=sqlite3.connect(self.path)
        source.backup(self.conn)
        source.close()
        self.conn.row_factory=sqlite3.Row
        self.conn.execute('PRAGMA foreign_keys=ON')
        self.conn.execute('BEGIN IMMEDIATE')
        apply_schema(self.conn)
        self.conn.commit()
        self.chain=seed_chain(self.conn)
        self.auth=seed_authorization(self.conn,self.chain)
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def approve(self, **changes):
        return approve_settlement_eligibility(self.conn,**{**self.chain,**changes})

    def settlement(self, **changes):
        fields=dict(task_id=self.chain['task_id'],obligation_id='test-obligation-'+self.chain['task_id'],
            contract_acceptance_decision_id=self.chain['contract_acceptance_decision_id'],payment_authorization_id=self.auth,
            amount_cents=100,currency='AUD',provider='TEST_PROVIDER',environment='TEST')
        return create_settlement(self.conn,**{**fields,**changes})

    def test_successful_eligibility_and_exact_replay(self):
        first=self.approve()
        second=self.approve()
        self.assertEqual(first,second)
        self.assertEqual(first['producer_id'],'test-producer')
        self.assertNotEqual(first['producer_id'],first['validator_id'])
        self.assertEqual(first['approved'],1)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM settlement_eligibility_decisions WHERE task_id=?',(self.chain['task_id'],)).fetchone()[0],1)

    def test_successful_settlement_and_exact_replay(self):
        decision=self.approve()['decision_id']
        first=self.settlement(eligibility_decision_id=decision)
        second=self.settlement(eligibility_decision_id=decision)
        self.assertEqual(first,second)
        self.assertEqual(first['state'],'PAYMENT_AUTHORIZED')
        self.assertEqual(first['eligibility_decision_id'],decision)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM canonical_settlement_journal WHERE settlement_id=?',(first['settlement_id'],)).fetchone()[0],1)

    def test_settlement_rejected_without_eligibility(self):
        for decision in (None,'','missing-id'):
            with self.subTest(decision=decision), self.assertRaises(ValueError):
                self.settlement(eligibility_decision_id=decision)

    def test_settlement_wrong_task_environment_acceptance(self):
        decision=self.approve()['decision_id']
        for changes in ({'task_id':'wrong-task'},{'environment':'SANDBOX'},
                {'contract_acceptance_decision_id':'wrong-acceptance'}):
            with self.subTest(changes=changes),self.assertRaises(ValueError):
                self.settlement(eligibility_decision_id=decision,**changes)

    def test_conflicting_settlement_replay(self):
        decision=self.approve()['decision_id']
        self.settlement(eligibility_decision_id=decision)
        for changes in ({'amount_cents':101},{'currency':'USD'},{'payment_authorization_id':'wrong-auth'}):
            with self.subTest(changes=changes),self.assertRaises(ValueError):
                self.settlement(eligibility_decision_id=decision,**changes)

    def test_missing_producer_and_commit(self):
        for options in ({'producer_id':None},{'producer_id':''},{'commit':None},{'commit':''}):
            chain=seed_chain(self.conn,**options)
            with self.subTest(options=options),self.assertRaises(SettlementEligibilityError):
                approve_settlement_eligibility(self.conn,**chain)

    def test_same_producer_validator(self):
        chain=seed_chain(self.conn,validator_id='test-producer')
        with self.assertRaises(SettlementEligibilityError):
            approve_settlement_eligibility(self.conn,**chain)

    def test_missing_validator(self):
        chain=seed_chain(self.conn,validator_id='')
        with self.assertRaises(SettlementEligibilityError):
            approve_settlement_eligibility(self.conn,**chain)

    def test_evidence_delivery_commit_mismatch(self):
        chain=seed_chain(self.conn,evidence_commit='b'*40)
        with self.assertRaises(SettlementEligibilityError):
            approve_settlement_eligibility(self.conn,**chain)

    def test_wrong_identifiers(self):
        other=seed_chain(self.conn)
        for key in ('task_id','work_delivery_id','evidence_id','validation_result_id',
                'technical_decision_id','contract_acceptance_decision_id'):
            with self.subTest(key=key),self.assertRaises(SettlementEligibilityError):
                self.approve(**{key:other[key]})
        with self.assertRaises(SettlementEligibilityError):
            self.approve(environment='SANDBOX')

    def test_rejected_technical_decision(self):
        self.conn.execute('UPDATE technical_decisions SET accepted=0 WHERE decision_id=?',(self.chain['technical_decision_id'],))
        with self.assertRaises(SettlementEligibilityError):
            self.approve()

    def test_rejected_contract(self):
        self.conn.execute('UPDATE contract_acceptance_decisions SET accepted=0 WHERE decision_id=?',(self.chain['contract_acceptance_decision_id'],))
        with self.assertRaises(SettlementEligibilityError):
            self.approve()

    def test_validation_evidence_linkage(self):
        other=seed_chain(self.conn)
        self.conn.execute('UPDATE validation_results SET evidence_id=? WHERE result_id=?',(other['evidence_id'],self.chain['validation_result_id']))
        with self.assertRaises(SettlementEligibilityError):
            self.approve()

    def test_technical_validation_linkage(self):
        other=seed_chain(self.conn)
        self.conn.execute('UPDATE technical_decisions SET validation_result_id=? WHERE decision_id=?',
            (other['validation_result_id'],self.chain['technical_decision_id']))
        with self.assertRaises(SettlementEligibilityError):
            self.approve()

    def test_technical_evidence_linkage(self):
        other=seed_chain(self.conn)
        self.conn.execute("UPDATE technical_decisions SET evidence_id=?,policy_name='mismatched-evidence-policy' WHERE decision_id=?",
            (other['evidence_id'],self.chain['technical_decision_id']))
        with self.assertRaises(SettlementEligibilityError):
            self.approve()

    def test_validation_not_passed(self):
        self.conn.execute("UPDATE validation_results SET status='INCONCLUSIVE' WHERE result_id=?",(self.chain['validation_result_id'],))
        with self.assertRaises(SettlementEligibilityError):
            self.approve()

    def test_wrong_contractual_delivery_and_environment(self):
        other=seed_chain(self.conn)
        event=self.conn.execute('SELECT acceptance_event_id FROM contract_acceptance_decisions WHERE decision_id=?',
            (self.chain['contract_acceptance_decision_id'],)).fetchone()[0]
        self.conn.execute('UPDATE external_acceptance_events SET work_delivery_id=? WHERE event_id=?',(other['work_delivery_id'],event))
        with self.assertRaises(SettlementEligibilityError):
            self.approve()
        self.conn.execute("UPDATE external_acceptance_events SET work_delivery_id=?,environment='SANDBOX' WHERE event_id=?",(self.chain['work_delivery_id'],event))
        with self.assertRaises(SettlementEligibilityError):
            self.approve()

    def test_artifact_and_dependencies_immutable(self):
        decision=self.approve()['decision_id']
        statements=(
            ('UPDATE settlement_eligibility_decisions SET validator_id=? WHERE decision_id=?',('forged',decision)),
            ('DELETE FROM settlement_eligibility_decisions WHERE decision_id=?',(decision,)),
            ('UPDATE raw_evidence SET git_commit_hash=? WHERE evidence_id=?',('b'*40,self.chain['evidence_id'])),
            ('UPDATE validation_results SET validator_id=? WHERE result_id=?',('forged',self.chain['validation_result_id'])),
            ('UPDATE technical_decisions SET accepted=0 WHERE decision_id=?',(self.chain['technical_decision_id'],)),
            ('UPDATE contract_acceptance_decisions SET accepted=0 WHERE decision_id=?',(self.chain['contract_acceptance_decision_id'],)))
        for sql,args in statements:
            with self.subTest(sql=sql),self.assertRaises(sqlite3.IntegrityError):
                self.conn.execute(sql,args)

    def test_delivery_provenance_immutable_even_before_approval(self):
        for column in ('producer_id','dispatched_commit_hash'):
            with self.subTest(column=column),self.assertRaises(sqlite3.IntegrityError):
                self.conn.execute(f'UPDATE work_deliveries SET {column}=? WHERE delivery_id=?',('different',self.chain['work_delivery_id']))

    def test_direct_forged_artifact_rejected(self):
        decision=self.approve()
        forged={k:v for k,v in decision.items()}
        forged['decision_id']='forged-approval'
        forged['producer_id']='forged-producer'
        forged['task_id']='forged-task'
        columns=list(forged)
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute('INSERT INTO settlement_eligibility_decisions ('+','.join(columns)+') VALUES ('+
                ','.join(':'+c for c in columns)+')',forged)

    def test_direct_settlement_insert_without_eligibility_rejected(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("""INSERT INTO canonical_settlements(settlement_id,task_id,obligation_id,
                contract_acceptance_decision_id,payment_authorization_id,amount_cents,currency,state,provider,
                environment,created_at,updated_at) VALUES('forged-settlement',?,'forged-obligation',?,?,100,
                'AUD','PAYMENT_AUTHORIZED','TEST_PROVIDER','TEST','now','now')""",
                (self.chain['task_id'],self.chain['contract_acceptance_decision_id'],self.auth))

    def test_delivery_exact_replay_and_conflicts(self):
        args=dict(task_id='delivery-replay-task',repository='example-owner/replay-repo',pull_request_number=876543,
            pull_request_url='https://github.com/example-owner/replay-repo/pull/876543',environment='TEST',
            producer_id='producer-one',dispatched_commit_hash='c'*40)
        with patch.object(db,'DB_PATH',str(self.path)),patch.object(db,'DB_DIR',self.tmp.name):
            first=create_delivery(**args)
            second=create_delivery(**args)
            self.assertEqual(dict(first),dict(second))
            for changes in ({'producer_id':'producer-two'},{'dispatched_commit_hash':'d'*40},
                    {'producer_id':None},{'dispatched_commit_hash':None}):
                with self.subTest(changes=changes),self.assertRaises(WorkDeliveryError):
                    create_delivery(**{**args,**changes})

    def test_contract_wrong_task_and_unauthenticated_event(self):
        for mutation in ('wrong-task','unauthenticated'):
            chain=seed_chain(self.conn)
            event=self.conn.execute('SELECT acceptance_event_id FROM contract_acceptance_decisions WHERE decision_id=?',
                (chain['contract_acceptance_decision_id'],)).fetchone()[0]
            if mutation=='wrong-task':
                self.conn.execute("UPDATE contract_acceptance_decisions SET task_id='other-task' WHERE decision_id=?",
                    (chain['contract_acceptance_decision_id'],))
            else:
                self.conn.execute('UPDATE external_acceptance_events SET authenticated=0 WHERE event_id=?',(event,))
            with self.subTest(mutation=mutation),self.assertRaises(SettlementEligibilityError):
                approve_settlement_eligibility(self.conn,**chain)

    def test_technical_wrong_environment(self):
        self.conn.execute("UPDATE technical_decisions SET environment='SANDBOX' WHERE decision_id=?",(self.chain['technical_decision_id'],))
        with self.assertRaises(SettlementEligibilityError):
            self.approve()

    def test_missing_identifiers_are_not_evidence(self):
        for key in self.chain:
            with self.subTest(key=key),self.assertRaises(SettlementEligibilityError):
                self.approve(**{key:None})

    def test_delivery_replacement_forbidden(self):
        row=dict(self.conn.execute('SELECT * FROM work_deliveries WHERE delivery_id=?',(self.chain['work_delivery_id'],)).fetchone())
        row['producer_id']='replacement-actor'
        columns=list(row)
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute('INSERT OR REPLACE INTO work_deliveries ('+','.join(columns)+') VALUES ('+
                ','.join(':'+c for c in columns)+')',row)

    def test_independent_approval_does_not_change_financial_state(self):
        tables=('payment_authorizations','canonical_settlements','canonical_settlement_journal','provider_events','revenue_ledger')
        before={table:[tuple(r) for r in self.conn.execute('SELECT * FROM '+table)] for table in tables}
        self.approve()
        after={table:[tuple(r) for r in self.conn.execute('SELECT * FROM '+table)] for table in tables}
        self.assertEqual(before,after)

    def test_financial_authorization_checks_remain_enforced(self):
        decision=self.approve()['decision_id']
        for changes in ({'amount_cents':101},{'currency':'USD'},{'provider':'OTHER'},
                {'payment_authorization_id':'missing-auth'}):
            with self.subTest(changes=changes),self.assertRaises(ValueError):
                self.settlement(eligibility_decision_id=decision,**changes)
        self.conn.execute('UPDATE payment_authorizations SET verified=0 WHERE authorization_id=?',(self.auth,))
        with self.assertRaises(ValueError):
            self.settlement(eligibility_decision_id=decision)

    def test_settlement_engine_knows_only_artifact_semantics(self):
        import ast
        import canonical_settlement_engine as engine
        tree=ast.parse(Path(engine.__file__).read_text(encoding='utf-8-sig'))
        method=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='create_settlement')
        strings=[n.value for n in ast.walk(method) if isinstance(n,ast.Constant) and isinstance(n.value,str)]
        self.assertFalse(any(any(table in text for table in ('work_deliveries','raw_evidence','validation_results','technical_decisions')) for text in strings))

    def test_eligibility_replace_and_delete_forbidden(self):
        decision=self.approve()
        columns=list(decision)
        for mode in ('INSERT OR REPLACE','REPLACE'):
            with self.subTest(mode=mode),self.assertRaises(sqlite3.IntegrityError):
                self.conn.execute(mode+' INTO settlement_eligibility_decisions ('+','.join(columns)+') VALUES ('+
                    ','.join(':'+c for c in columns)+')',decision)

    def test_existing_settlement_cannot_be_replaced_or_rebound(self):
        decision=self.approve()['decision_id']
        settlement=self.settlement(eligibility_decision_id=decision)
        columns=list(settlement)
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute('INSERT OR REPLACE INTO canonical_settlements ('+','.join(columns)+') VALUES ('+
                ','.join(':'+c for c in columns)+')',settlement)
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute('UPDATE canonical_settlements SET eligibility_decision_id=NULL WHERE settlement_id=?',
                (settlement['settlement_id'],))

    def test_approval_and_settlement_restart_replay(self):
        decision=self.approve()['decision_id']
        first=self.settlement(eligibility_decision_id=decision)
        self.conn.commit()
        second=sqlite3.connect(self.path)
        second.row_factory=sqlite3.Row
        second.execute('PRAGMA foreign_keys=ON')
        try:
            artifact=approve_settlement_eligibility(second,**self.chain)
            self.assertEqual(artifact['decision_id'],decision)
            replay=create_settlement(second,task_id=self.chain['task_id'],obligation_id=first['obligation_id'],
                contract_acceptance_decision_id=self.chain['contract_acceptance_decision_id'],payment_authorization_id=self.auth,
                eligibility_decision_id=decision,amount_cents=100,currency='AUD',provider='TEST_PROVIDER',environment='TEST')
            self.assertEqual(first,replay)
        finally:
            second.close()

    def test_historical_database_migration_preserves_records(self):
        # Copy the pre-migration original backup/source into a separate fresh database.
        historical=sqlite3.connect(':memory:')
        historical.row_factory=sqlite3.Row
        historical.execute('PRAGMA foreign_keys=ON')
        # Construct prior schema from the existing forward migrations, with network unused.
        with tempfile.TemporaryDirectory(prefix='history-migration-',dir=Path(__file__).parent/'data') as tmp:
            old_path=Path(tmp)/'old.db'
            with patch.object(db,'DB_PATH',str(old_path)),patch.object(db,'DB_DIR',tmp):
                import contextlib,io
                with contextlib.redirect_stdout(io.StringIO()):
                    import migration_001_database_foundation as m1
                    import migration_002_canonical_marketplace as m2
                    import migration_003_canonical_settlement_model as m3
                    import migration_004_work_delivery_provenance as m4
                    import migration_005_acceptance_provenance as m5
                    import migration_006_payment_authorization_evidence as m6
                    db.init_db(); m1.apply_migration(); m2.apply_migration(); m3.apply_migration(); m4.migrate(); m5.migrate(); m6.migrate()
                old=sqlite3.connect(old_path)
                old.backup(historical)
                old.close()
        historical.execute("""INSERT INTO work_deliveries(delivery_id,task_id,repository,pull_request_number,
            pull_request_url,status,environment,dispatched_at,created_at,updated_at)
            VALUES('old-delivery','old-task','example/old',1,'https://github.com/example/old/pull/1',
            'DISPATCHED','SANDBOX','old','old','old')""")
        historical.execute("""INSERT INTO external_acceptance_events(event_id,task_id,source,external_event_id,
            event_type,authenticated,environment,observed_at,created_at,work_delivery_id)
            VALUES('old-event','old-task','TEST_HARNESS','old-external','TEST_ACCEPTANCE',1,'SANDBOX','old','old','old-delivery')""")
        historical.execute("""INSERT INTO contract_acceptance_decisions(decision_id,task_id,acceptance_event_id,
            policy_name,policy_version,accepted,environment,created_at)
            VALUES('old-contract','old-task','old-event','old-policy','1',1,'SANDBOX','old')""")
        historical.execute("""INSERT INTO payment_authorizations(authorization_id,task_id,contract_acceptance_decision_id,
            provider,provider_authorization_id,amount_cents,currency,provider_status,verified,environment,created_at,updated_at)
            VALUES('old-auth','old-task','old-contract','TEST_PROVIDER','old-provider-auth',100,'AUD','AUTHORIZED',1,'SANDBOX','old','old')""")
        historical.execute("""INSERT INTO canonical_settlements(settlement_id,task_id,obligation_id,contract_acceptance_decision_id,
            payment_authorization_id,amount_cents,currency,state,provider,provider_capture_id,environment,created_at,updated_at)
            VALUES('old-settlement','old-task','old-obligation','old-contract','old-auth',100,'AUD','REVENUE_RECORDED',
            'TEST_PROVIDER','old-capture','SANDBOX','old','old')""")
        historical.execute("""INSERT INTO revenue_ledger(ledger_id,settlement_id,task_id,provider,provider_tx_id,
            amount_cents,currency,environment,recorded_at)
            VALUES('old-ledger','old-settlement','old-task','TEST_PROVIDER','old-capture',100,'AUD','SANDBOX','old')""")
        historical.execute("""INSERT INTO canonical_settlement_journal(settlement_id,from_state,to_state,reason,environment,created_at)
            VALUES('old-settlement','PROVIDER_CONFIRMED','REVENUE_RECORDED','historical fixture','SANDBOX','old')""")
        financial_before={table:[tuple(r) for r in historical.execute('SELECT * FROM '+table)] for table in
            ('canonical_settlements','revenue_ledger','canonical_settlement_journal','payment_authorizations')}
        before=[tuple(r) for r in historical.execute('SELECT * FROM work_deliveries')]
        historical.commit()
        historical.execute('BEGIN IMMEDIATE')
        apply_schema(historical)
        historical.commit()
        rows=historical.execute('SELECT * FROM work_deliveries').fetchall()
        self.assertEqual([tuple(r)[:-1] for r in rows],before)
        self.assertIsNone(rows[0]['producer_id'])
        for table, original in financial_before.items():
            after=[tuple(r) for r in historical.execute('SELECT * FROM '+table)]
            if table=='canonical_settlements':
                self.assertEqual([r[:-1] for r in after],original)
                self.assertIsNone(after[0][-1])
            else:
                self.assertEqual(after,original)
        self.assertEqual(get_settlement(historical,'old-settlement')['state'],'REVENUE_RECORDED')

        self.assertEqual(historical.execute('SELECT count(*) FROM settlement_eligibility_decisions').fetchone()[0],0)
        with self.assertRaises(sqlite3.IntegrityError):
            historical.execute("UPDATE work_deliveries SET producer_id='invented' WHERE delivery_id='old-delivery'")
        with self.assertRaises(SettlementEligibilityError):
            approve_settlement_eligibility(historical,task_id='old-task',work_delivery_id='old-delivery',evidence_id='missing',
                validation_result_id=1,technical_decision_id='missing',contract_acceptance_decision_id='missing',environment='SANDBOX')
        apply_schema(historical)
        self.assertEqual(historical.execute('PRAGMA integrity_check').fetchone()[0],'ok')
        self.assertEqual(historical.execute('PRAGMA foreign_key_check').fetchall(),[])
        historical.close()

if __name__=='__main__':
    unittest.main(argv=['eligibility-tests'],exit=True)
