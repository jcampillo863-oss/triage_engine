"""Automated synthetic authorization integration; isolated DB, no provider imports/calls."""
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import uuid
import db
from github_acceptance import normalize_pull_request_event
from work_delivery import create_delivery
from external_acceptance import process_github_acceptance_event
from paypal_adapter import normalize_authorization
from payment_authority import establish_payment_authorization

class SyntheticPayPalIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='canonical-integration-',dir=Path(__file__).parent/'data')
        self.path=Path(self.tmp.name)/'test.db'
        with closing(sqlite3.connect(Path(db.DB_PATH).resolve().as_uri()+'?mode=ro',uri=True)) as source:
            with closing(sqlite3.connect(self.path)) as target:source.backup(target)
        self.path_patch=patch.object(db,'DB_PATH',str(self.path));self.path_patch.start()
        self.dir_patch=patch.object(db,'DB_DIR',self.tmp.name);self.dir_patch.start()
        suffix=uuid.uuid4().hex
        self.task='synthetic-integration-task-'+suffix
        self.order_id='synthetic-order-'+suffix
        self.auth_id='synthetic-authorization-'+suffix
        self.repo='example-owner/synthetic-integration'
        self.pr=123456
        self.url=f'https://github.com/{self.repo}/pull/{self.pr}'
        delivery=create_delivery(task_id=self.task,repository=self.repo,pull_request_number=self.pr,
            pull_request_url=self.url,environment='TEST',producer_id='synthetic-producer',dispatched_commit_hash='a'*40)
        event=normalize_pull_request_event(dict(action='closed',repository={'full_name':self.repo},
            pull_request={'number':self.pr,'html_url':self.url,'merged':True,'merge_commit_sha':'b'*40}),
            delivery_id='synthetic-delivery-'+suffix,expected_repository=self.repo)
        acceptance=process_github_acceptance_event(event,environment='TEST')
        self.contract=acceptance['decision']['decision_id']
        self.order={'id':self.order_id,'purchase_units':[{'payments':{'authorizations':[
            {'id':self.auth_id,'status':'CREATED','amount':{'value':'1.00','currency_code':'AUD'}}]}}]}

    def tearDown(self):
        self.dir_patch.stop();self.path_patch.stop();self.tmp.cleanup()

    def authorize(self, amount=100):
        # Injected provider-shaped read fixture; authenticated=True denotes test evidence only.
        calls=[]
        def lookup(identity):
            calls.append(identity)
            if identity!=self.order_id:raise ValueError('Synthetic lookup identity mismatch')
            return self.order
        observed=lookup(self.order_id)
        authorizations=observed['purchase_units'][0]['payments']['authorizations']
        if observed['id']!=self.order_id or len(authorizations)!=1:
            raise ValueError('Synthetic order authorization identity is ambiguous')
        event=normalize_authorization(authorizations[0],provider_event_id='synthetic-api-order-'+self.order_id,authenticated=True)
        establish_payment_authorization(provider_event=event,task_id=self.task,
            contract_acceptance_decision_id=self.contract,expected_amount_cents=amount,
            expected_currency='AUD',expected_provider='paypal',environment='TEST')
        return calls

    def test_synthetic_authorization_integration(self):
        self.assertEqual(self.authorize(),[self.order_id])
        with db.get_db() as c:
            rows=c.execute('SELECT * FROM payment_authorizations WHERE task_id=?',(self.task,)).fetchall()
            self.assertEqual(len(rows),1)
            self.assertEqual(rows[0]['provider_authorization_id'],self.auth_id)
            self.assertEqual((rows[0]['verified'],rows[0]['amount_cents'],rows[0]['currency']),(1,100,'AUD'))
            self.assertEqual(c.execute('SELECT count(*) FROM canonical_settlements WHERE task_id=?',(self.task,)).fetchone()[0],0)
            self.assertEqual(c.execute('SELECT count(*) FROM revenue_ledger WHERE task_id=?',(self.task,)).fetchone()[0],0)

    def test_exact_authorization_replay(self):
        self.authorize();self.authorize()
        with db.get_db() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM payment_authorizations WHERE task_id=?',(self.task,)).fetchone()[0],1)

    def test_amount_mismatch_rejected(self):
        self.authorize(amount=200)
        with db.get_db() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM payment_authorizations WHERE task_id=?',(self.task,)).fetchone()[0],0)
            verification=c.execute('SELECT verified FROM payment_authorization_verifications WHERE task_id=?',(self.task,)).fetchone()
            self.assertIsNotNone(verification)
            self.assertEqual(verification['verified'],0)

    def test_ambiguous_order_authorizations_rejected(self):
        self.order['purchase_units'][0]['payments']['authorizations']*=2
        with self.assertRaises(ValueError):self.authorize()

if __name__=='__main__':unittest.main(argv=['synthetic-integration'],exit=True)
