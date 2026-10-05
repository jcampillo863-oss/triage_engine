"""Principal/capability tests: isolated DB, generated test-only secrets, no providers."""
from contextlib import closing
from dataclasses import fields
from pathlib import Path
import io
import json
import logging
import os
import secrets
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid
import db
import service_principals as principals
from authenticated_work import record_work_evidence, validate_work, AuthorityOperationError
from work_delivery import create_delivery
from validation import LocalValidator, RawEvidence, ValidationResult, ValidationStatus, save_evidence_and_result
from acceptance_policy import PolicyDecision, save_policy_decision
from settlement_eligibility import approve_settlement_eligibility, SettlementEligibilityError
from external_acceptance import process_github_acceptance_event

class PrincipalTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='principal-tests-',dir=Path(__file__).parent/'data')
        self.path=Path(self.tmp.name)/'test.db'
        with closing(sqlite3.connect(Path(db.DB_PATH).resolve().as_uri()+'?mode=ro',uri=True)) as source:
            with closing(sqlite3.connect(self.path)) as target:
                source.backup(target)
        self.producer_secret=secrets.token_urlsafe(32)
        self.validator_secret=secrets.token_urlsafe(32)
        self.env=patch.dict(os.environ,{principals.PRODUCER_SECRET_ENV:self.producer_secret,
            principals.VALIDATOR_SECRET_ENV:self.validator_secret},clear=True)
        self.env.start()
        self.db_path=patch.object(db,'DB_PATH',str(self.path)); self.db_path.start()
        self.db_dir=patch.object(db,'DB_DIR',self.tmp.name); self.db_dir.start()
        self.log=io.StringIO()
        self.handler=logging.StreamHandler(self.log)
        self.logger=logging.getLogger('marketplace.authority')
        self.old_level=self.logger.level
        self.logger.setLevel(logging.INFO); self.logger.addHandler(self.handler)
        self.args=dict(task_id='principal-test-'+uuid.uuid4().hex,repository='example-owner/principal-test',
            pull_request_number=765432,pull_request_url='https://github.com/example-owner/principal-test/pull/765432',
            environment='PRODUCTION',dispatched_commit_hash='a'*40)

    def tearDown(self):
        self.logger.removeHandler(self.handler); self.logger.setLevel(self.old_level); self.handler.close()
        self.db_dir.stop(); self.db_path.stop(); self.env.stop(); self.tmp.cleanup()

    def produce(self,**changes):
        return create_delivery(**{**self.args,'producer_credential':self.producer_secret,**changes})

    def evidence(self,delivery,**changes):
        args=dict(producer_credential=self.producer_secret,work_delivery_id=delivery['delivery_id'],
            evidence_id='principal-evidence-'+uuid.uuid4().hex,pytest_exit_code=0,syntax_valid=True,
            compilation_valid=True,diff_policy_passed=True,raw_payload={'synthetic':True})
        return record_work_evidence(**{**args,**changes})

    def validate(self,delivery,evidence,**changes):
        return validate_work(**{**dict(validator_credential=self.validator_secret,work_delivery_id=delivery['delivery_id'],
            evidence_id=evidence['evidence_id']),**changes})

    def test_correct_producer_and_validator_authentication(self):
        producer=principals.authenticate(self.producer_secret,principals.PRODUCE_WORK)
        validator=principals.authenticate(self.validator_secret,principals.VALIDATE_WORK)
        self.assertEqual(producer.principal_id,principals.PRODUCER_ID)
        self.assertEqual(producer.capability,principals.PRODUCE_WORK)
        self.assertEqual(validator.principal_id,principals.VALIDATOR_ID)
        self.assertEqual(validator.capability,principals.VALIDATE_WORK)
        self.assertEqual({f.name for f in fields(producer)},{'principal_id','capability'})
        self.assertNotEqual(producer.principal_id,validator.principal_id)

    def test_cross_capability_rejection(self):
        for credential,capability in ((self.producer_secret,principals.VALIDATE_WORK),
                (self.validator_secret,principals.PRODUCE_WORK)):
            with self.assertRaises(principals.PrincipalAuthenticationError):
                principals.authenticate(credential,capability)
        with self.assertRaises(principals.PrincipalAuthenticationError):
            self.produce(producer_credential=self.validator_secret)
        delivery=self.produce(); evidence=self.evidence(delivery)
        with self.assertRaises(principals.PrincipalAuthenticationError):
            self.validate(delivery,evidence,validator_credential=self.producer_secret)
        with self.assertRaises(principals.PrincipalAuthenticationError):
            self.evidence(delivery,producer_credential=self.validator_secret)

    def test_wrong_missing_malformed_credentials(self):
        for credential in (None,'',b'not-a-string',{},'short','not valid'+('x'*50),secrets.token_urlsafe(32)):
            for capability in (principals.PRODUCE_WORK,principals.VALIDATE_WORK):
                with self.assertRaises(principals.PrincipalAuthenticationError):
                    principals.authenticate(credential,capability)
        with self.assertRaises(principals.PrincipalAuthenticationError):
            self.produce(producer_credential=None)

    def test_absent_and_malformed_configuration(self):
        for key in (principals.PRODUCER_SECRET_ENV,principals.VALIDATOR_SECRET_ENV):
            for value in ('','short'):
                with patch.dict(os.environ,{key:value}):
                    with self.assertRaises(principals.PrincipalAuthenticationError):
                        principals.authenticate(self.producer_secret,principals.PRODUCE_WORK)
        with patch.dict(os.environ,{},clear=True):
            with self.assertRaises(principals.PrincipalAuthenticationError):
                self.produce()
        with patch.dict(os.environ,{principals.VALIDATOR_SECRET_ENV:self.producer_secret}):
            with self.assertRaises(principals.PrincipalAuthenticationError):
                self.produce()

    def test_producer_identity_spoofing(self):
        for identity in ('attacker',principals.PRODUCER_ID,principals.VALIDATOR_ID,None):
            with self.assertRaises(principals.PrincipalAuthenticationError):
                self.produce(producer_id=identity)
        delivery=self.produce()
        self.assertEqual(delivery['producer_id'],principals.PRODUCER_ID)
        for identity in ('attacker',None):
            with self.assertRaises(principals.PrincipalAuthenticationError):
                self.evidence(delivery,producer_id=identity)

    def test_validator_identity_spoofing(self):
        delivery=self.produce(); evidence=self.evidence(delivery)
        for identity in ('attacker',principals.VALIDATOR_ID,principals.PRODUCER_ID,None):
            with self.assertRaises(principals.PrincipalAuthenticationError):
                self.validate(delivery,evidence,validator_id=identity)
        result=self.validate(delivery,evidence)
        self.assertEqual(result['validator_id'],principals.VALIDATOR_ID)
        with db.get_db() as c:
            row=c.execute('SELECT validator_id FROM validation_results WHERE result_id=?',(result['validation_result_id'],)).fetchone()
            self.assertEqual(row['validator_id'],principals.VALIDATOR_ID)

    def test_production_default_and_explicit_validator_rejected(self):
        for args in ({},{'environment':'PRODUCTION'},
                {'validator_id':principals.VALIDATOR_ID,'environment':'PRODUCTION'}):
            with self.assertRaises(principals.PrincipalAuthenticationError):
                LocalValidator(**args)
        delivery=self.produce(); evidence=self.evidence(delivery)
        with self.assertRaises(principals.PrincipalAuthenticationError):
            self.validate(delivery,evidence,validator_credential=None)
        self.assertEqual(LocalValidator(environment='TEST').validator_id,'local_precision_v1')
        self.assertEqual(LocalValidator('sandbox-validator',environment='SANDBOX').validator_id,'sandbox-validator')

    def test_compatibility_persistence_production_rejected(self):
        delivery=self.produce()
        evidence=RawEvidence(task_id=delivery['task_id'],git_commit_hash='a'*40,pytest_exit_code=0,
            syntax_valid=True,compilation_valid=True,diff_policy_passed=True,raw_payload={})
        result=LocalValidator(environment='TEST').evaluate(evidence)
        for environment in (None,'PRODUCTION','TEST','SANDBOX'):
            with self.assertRaises(ValueError):
                save_evidence_and_result(evidence,result,environment=environment)
        recorded=self.evidence(delivery)
        policy=PolicyDecision(evidence_id=recorded['evidence_id'])
        for environment in (None,'PRODUCTION','TEST'):
            with self.assertRaises(ValueError):
                save_policy_decision(policy,environment=environment)

    def test_complete_authenticated_chain_preserves_eligibility(self):
        delivery=self.produce(); evidence=self.evidence(delivery); validation=self.validate(delivery,evidence)
        # Synthetic contractual evidence ONLY, not a genuine GitHub delivery or external call.
        contract=process_github_acceptance_event(dict(source='github',external_event_id='principal-test-event',
            event_type='pull_request',action='closed',repository=delivery['repository'],
            pull_request_number=delivery['pull_request_number'],pull_request_url=delivery['pull_request_url'],
            merged=True,merge_commit_sha='b'*40,authenticated=True),environment='PRODUCTION')
        with db.get_db() as c:
            before=c.execute('SELECT count(*) FROM canonical_settlements').fetchone()[0]
            approval=approve_settlement_eligibility(c,task_id=delivery['task_id'],work_delivery_id=delivery['delivery_id'],
                evidence_id=evidence['evidence_id'],validation_result_id=validation['validation_result_id'],
                technical_decision_id=validation['technical_decision_id'],contract_acceptance_decision_id=contract['decision']['decision_id'],environment='PRODUCTION')
            self.assertEqual(approval['producer_id'],principals.PRODUCER_ID)
            self.assertEqual(approval['validator_id'],principals.VALIDATOR_ID)
            self.assertEqual(approval['approved'],1)
            self.assertEqual(before,c.execute('SELECT count(*) FROM canonical_settlements').fetchone()[0])

    def test_incomplete_evidence_never_positive(self):
        delivery=self.produce()
        for changes in ({'pytest_exit_code':None},{'pytest_exit_code':1},{'syntax_valid':False}):
            evidence=self.evidence(delivery,**changes)
            result=self.validate(delivery,evidence)
            self.assertFalse(result['accepted'])
        with self.assertRaises(AuthorityOperationError):
            self.evidence(delivery,syntax_valid=None)

    def test_exact_evidence_and_validator_replay(self):
        delivery=self.produce(); evidence=self.evidence(delivery)
        same=self.evidence(delivery,evidence_id=evidence['evidence_id'])
        self.assertEqual(evidence,same)
        with self.assertRaises(AuthorityOperationError):
            self.evidence(delivery,evidence_id=evidence['evidence_id'],pytest_exit_code=1)
        first=self.validate(delivery,evidence)
        second=self.validate(delivery,evidence)
        self.assertEqual(first,second)

    def test_wrong_task_commit_or_environment_fail_closed(self):
        delivery=self.produce(); evidence=self.evidence(delivery)
        for change in ({'environment':'TEST'},{'work_delivery_id':'unknown-delivery'},{'evidence_id':'unknown-evidence'}):
            with self.assertRaises(AuthorityOperationError):
                self.validate(delivery,evidence,**change)
        with db.get_db() as c:
            c.execute('UPDATE raw_evidence SET git_commit_hash=? WHERE evidence_id=?',('c'*40,evidence['evidence_id'])); c.commit()
        with self.assertRaises(AuthorityOperationError):
            self.validate(delivery,evidence)

    def test_credential_material_cannot_be_stored(self):
        with self.assertRaises(principals.PrincipalAuthenticationError):
            self.produce(task_id=self.producer_secret)
        delivery=self.produce()
        for secret in (self.producer_secret,self.validator_secret):
            with self.assertRaises(principals.PrincipalAuthenticationError):
                self.evidence(delivery,raw_payload={'nested':{'credential':secret}})
        with db.get_db() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM raw_evidence WHERE task_id=?',(delivery['task_id'],)).fetchone()[0],0)

    def test_credentials_not_in_outputs_logs_errors_or_database(self):
        delivery=self.produce(); evidence=self.evidence(delivery); result=self.validate(delivery,evidence)
        principal=principals.authenticate(self.producer_secret,principals.PRODUCE_WORK)
        try:
            principals.authenticate(self.validator_secret,principals.PRODUCE_WORK)
        except principals.PrincipalAuthenticationError as error:
            error_text=str(error)
        with db.get_db() as c:
            persisted='\n'.join(c.iterdump())
        output=repr(principal)+repr(dict(delivery))+repr(evidence)+repr(result)+error_text+self.log.getvalue()+persisted
        self.assertTrue(all(secret not in output for secret in (self.producer_secret,self.validator_secret)),
            'Credential non-disclosure invariant failed')

    def test_sanitized_authority_audit(self):
        delivery=self.produce(); evidence=self.evidence(delivery); self.validate(delivery,evidence)
        principals.audit_authority('untrusted\nheader','bad-capability','bad-operation','bad-outcome')
        events=[json.loads(line) for line in self.log.getvalue().splitlines()]
        self.assertTrue(events)
        for event in events:
            self.assertEqual(set(event),{'principal_id','capability','operation','outcome','timestamp'})
            self.assertTrue(event['timestamp'].endswith('+00:00'))
            self.assertTrue(event['principal_id'] in {principals.PRODUCER_ID,principals.VALIDATOR_ID,'unauthenticated'})
        self.assertTrue(any(event['operation']=='validate_work' and event['outcome']=='succeeded' for event in events))

    def test_constant_time_comparison_and_rotation_no_cache(self):
        original=principals.hmac.compare_digest
        calls=[]
        def observed(*args):
            calls.append(1)
            return original(*args)
        with patch.object(principals.hmac,'compare_digest',side_effect=observed):
            principals.authenticate(self.producer_secret,principals.PRODUCE_WORK)
        self.assertEqual(len(calls),3) # Configuration separation + both supplied-secret comparisons.
        replacement=secrets.token_urlsafe(32)
        with patch.dict(os.environ,{principals.PRODUCER_SECRET_ENV:replacement}):
            with self.assertRaises(principals.PrincipalAuthenticationError):
                principals.authenticate(self.producer_secret,principals.PRODUCE_WORK)
            self.assertEqual(principals.authenticate(replacement,principals.PRODUCE_WORK).principal_id,principals.PRODUCER_ID)

    def test_real_process_restart_and_idempotent_validation(self):
        delivery=self.produce(); evidence=self.evidence(delivery)
        child="import sys,json,os\nimport db\nfrom authenticated_work import validate_work\ndb.DB_PATH=sys.argv[1]\ndb.DB_DIR=os.path.dirname(db.DB_PATH)\nresult=validate_work(validator_credential=os.environ['MARKETPLACE_VALIDATOR_SECRET'],work_delivery_id=sys.argv[2],evidence_id=sys.argv[3])\nprint(json.dumps(result,sort_keys=True))\n"
        results=[]
        for _ in range(2):
            completed=subprocess.run([sys.executable,'-c',child,str(self.path),delivery['delivery_id'],evidence['evidence_id']],
                cwd=Path(__file__).parent,env=dict(os.environ),capture_output=True,text=True,timeout=15,
                creationflags=subprocess.CREATE_NO_WINDOW)
            self.assertEqual(completed.returncode,0,'Isolated restart child failed')
            self.assertTrue(all(secret not in completed.stdout+completed.stderr for secret in (self.producer_secret,self.validator_secret)))
            results.append(json.loads(completed.stdout))
        self.assertEqual(results[0],results[1])
        self.assertEqual(results[0]['validator_id'],principals.VALIDATOR_ID)

    def test_malformed_capability_and_operation(self):
        for capability in (None, [], {}, 'UNKNOWN'):
            with self.assertRaises(principals.PrincipalAuthenticationError):
                principals.authenticate(self.producer_secret,capability)
        for operation in (None, [], {}, 'untrusted-operation'):
            with self.assertRaises(principals.PrincipalAuthenticationError):
                principals.authenticate(self.producer_secret,principals.PRODUCE_WORK,operation=operation)

    def test_production_provenance_cannot_be_assumed_for_old_identity(self):
        from eligibility_test_support import seed_chain
        with db.get_db() as c:
            chain=seed_chain(c,environment='PRODUCTION',producer_id='legacy-untrusted-producer')
            c.commit()
        with self.assertRaises(AuthorityOperationError):
            validate_work(validator_credential=self.validator_secret,work_delivery_id=chain['work_delivery_id'],evidence_id=chain['evidence_id'])

    def test_spoof_rejection_has_no_database_side_effect(self):
        with db.get_db() as c:
            before={table:c.execute('SELECT count(*) FROM '+table).fetchone()[0] for table in
                ('work_deliveries','raw_evidence','validation_results','technical_decisions')}
        with self.assertRaises(principals.PrincipalAuthenticationError):
            self.produce(producer_id=principals.PRODUCER_ID)
        with db.get_db() as c:
            after={table:c.execute('SELECT count(*) FROM '+table).fetchone()[0] for table in before}
        self.assertEqual(before,after)

    def test_audit_records_authentication_and_authority_rejections(self):
        with self.assertRaises(principals.PrincipalAuthenticationError):
            self.produce(producer_credential=self.validator_secret)
        with self.assertRaises(principals.PrincipalAuthenticationError):
            self.produce(producer_id='forged')
        events=[json.loads(line) for line in self.log.getvalue().splitlines()]
        self.assertTrue(any(event['principal_id']==principals.VALIDATOR_ID and event['capability']==principals.PRODUCE_WORK
            and event['outcome']=='rejected' for event in events))
        self.assertTrue(any(event['principal_id']==principals.PRODUCER_ID and event['operation']=='create_delivery'
            and event['outcome']=='rejected' for event in events))

    def test_authority_modules_do_not_import_acceptance_or_finance(self):
        import ast
        for name in ('service_principals.py','authenticated_work.py'):
            tree=ast.parse((Path(__file__).parent/name).read_text(encoding='utf-8'))
            imports=[n.module or '' for n in ast.walk(tree) if isinstance(n,ast.ImportFrom)]
            imports.extend(a.name for n in ast.walk(tree) if isinstance(n,ast.Import) for a in n.names)
            self.assertFalse(any(any(part in name for part in ('paypal','payment','revenue','settlement','github','acceptance_policy')) for name in imports))

if __name__=='__main__':
    unittest.main(argv=['principal-tests'],exit=True)
