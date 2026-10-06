"""Deterministic provider fixtures, isolated databases, no network or live credentials."""
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import socket
import sqlite3
import tempfile
import unittest
from datetime import datetime,timezone,timedelta
from contextlib import redirect_stdout,closing
from unittest.mock import patch
import canonical_bootstrap as bootstrap
from eligibility_test_support import seed_chain
from settlement_eligibility import approve_settlement_eligibility
from canonical_settlement_engine import create_settlement,transition_settlement,get_settlement
from paypal_client import PayPalClient,ProviderBoundaryError,ProviderEvidence,ENDPOINTS,request_identity,evidence_data
from production_payment_flow import (register_obligation,bind_order,approve_payment_authorization,
    capture_once,create_provider_order,authorize_provider_order,check_capture_approval)
from paypal_capture_service import prepare_capture,execute_capture,reconcile_capture
from payment_authority import establish_payment_authorization
from migration_009_payment_provider_boundary import apply_schema

class Response:
    def __init__(self,data,status=200):self.data=data;self.status_code=status
    def json(self):return copy.deepcopy(self.data)

class FakeSession:
    def __init__(self,test):self.test=test;self.trust_env=True
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def post(self,url,**kwargs):
        self.test.calls.append(('TOKEN',url,kwargs))
        self.test.assertFalse(self.trust_env)
        self.test.assertFalse(kwargs['allow_redirects'])
        expected=('fake-prod-id','fake-prod-secret') if 'sandbox' not in url else ('fake-sandbox-id','fake-sandbox-secret')
        self.test.assertEqual(kwargs['auth'],expected)
        return Response({'access_token':'fake-token'},self.test.oauth_status)
    def request(self,method,url,**kwargs):
        t=self.test;t.calls.append((method,url,kwargs))
        t.assertFalse(kwargs['allow_redirects'])
        if method=='POST':
            if url.endswith('/capture'):
                t.dispatches+=1
                with closing(sqlite3.connect(t.path)) as c:
                    t.assertEqual(c.execute('SELECT count(*) FROM canonical_capture_attempts').fetchone()[0],1)
                if t.capture_failure:raise TimeoutError('fake-prod-secret fake-token')
                if t.capture_crash:raise KeyboardInterrupt()
                return Response(t.capture)
            if url.endswith('/authorize') or url.endswith('/orders'):
                operation='authorize' if url.endswith('/authorize') else 'create'
                with closing(sqlite3.connect(t.path)) as c:
                    row=c.execute('SELECT request_id FROM payment_operation_claims WHERE obligation_id=? AND operation=?',('OBL1',operation)).fetchone()
                    t.assertEqual(row[0],kwargs['headers']['PayPal-Request-Id'])
                return Response(t.order,201)
            raise AssertionError('Unexpected mutation')
        if url.endswith('/orders/ORDER1'):return Response(t.order)
        if url.endswith('/authorizations/'+t.authorization['id']):return Response(t.authorization)
        if url.endswith('/captures/CAP1'):return Response(t.capture)
        raise AssertionError('Unexpected fake resource')

class PaymentBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base=tempfile.TemporaryDirectory(prefix='payment-boundary-base-')
        cls.reference=Path(cls.base.name)/'reference.db'
        with patch.dict(os.environ,{},clear=True),patch.object(socket.socket,'connect',side_effect=AssertionError('Network forbidden')):
            bootstrap.bootstrap(cls.reference)
    @classmethod
    def tearDownClass(cls):cls.base.cleanup()
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='payment-boundary-case-');self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'case.db'
        with closing(sqlite3.connect(self.reference)) as source:
            self.conn=sqlite3.connect(self.path);source.backup(self.conn)
        self.conn.row_factory=sqlite3.Row;self.conn.execute('PRAGMA foreign_keys=ON');self.addCleanup(self.conn.close)
        self.chain=seed_chain(self.conn,environment='PRODUCTION')
        self.eligibility=approve_settlement_eligibility(self.conn,**self.chain)
        self.obligation=register_obligation(self.conn,obligation_id='OBL1',task_id=self.chain['task_id'],
            eligibility_decision_id=self.eligibility['decision_id'],
            contract_acceptance_decision_id=self.chain['contract_acceptance_decision_id'],
            payee_id='PAYEE1',amount_cents=100,currency='AUD');self.conn.commit()
        self.calls=[];self.dispatches=0;self.oauth_status=200;self.capture_failure=False;self.capture_crash=False
        self.authorization={'id':'AUTH1','status':'CREATED','amount':{'value':'1.00','currency_code':'AUD'},
            'links':[{'rel':'up','href':'https://api-m.paypal.com/v2/checkout/orders/ORDER1'}]}
        self.order={'id':'ORDER1','intent':'AUTHORIZE','purchase_units':[{
            'reference_id':'OBL1','payee':{'merchant_id':'PAYEE1'},'amount':{'value':'1.00','currency_code':'AUD'},
            'payments':{'authorizations':[{'id':'AUTH1','status':'CREATED','amount':{'value':'1.00','currency_code':'AUD'}}]}}]}
        self.capture={'id':'CAP1','status':'COMPLETED','final_capture':True,
            'amount':{'value':'1.00','currency_code':'AUD'},
            'supplementary_data':{'related_ids':{'authorization_id':'AUTH1','order_id':'ORDER1'}}}
        self.env=patch.dict(os.environ,{'PAYPAL_PRODUCTION_CLIENT_ID':'fake-prod-id',
            'PAYPAL_PRODUCTION_CLIENT_SECRET':'fake-prod-secret','PAYPAL_SANDBOX_CLIENT_ID':'fake-sandbox-id',
            'PAYPAL_SANDBOX_CLIENT_SECRET':'fake-sandbox-secret'},clear=True);self.env.start();self.addCleanup(self.env.stop)
        for name in ('connect','connect_ex'):
            p=patch.object(socket.socket,name,side_effect=AssertionError('Network forbidden'));p.start();self.addCleanup(p.stop)
        p=patch('paypal_client.requests.Session',side_effect=lambda:FakeSession(self));p.start();self.addCleanup(p.stop)
        self.client=PayPalClient('PRODUCTION')
    def bind(self):
        bind_order(self.conn,self.client,'OBL1','ORDER1');self.conn.commit()
    def authorize(self):
        self.bind();receipt=self.client.observe_authorization('ORDER1','AUTH1')
        auth=approve_payment_authorization(self.conn,obligation_id='OBL1',provider_evidence=receipt);self.conn.commit()
        return auth
    def settlement(self):
        auth=self.authorize()
        s=create_settlement(self.conn,task_id=self.chain['task_id'],obligation_id='OBL1',
            contract_acceptance_decision_id=self.chain['contract_acceptance_decision_id'],
            payment_authorization_id=auth['authorization_id'],amount_cents=100,currency='AUD',
            provider='paypal',environment='PRODUCTION',eligibility_decision_id=self.eligibility['decision_id'])
        self.conn.commit();return s
    def gate(self,s,**changes):
        fields=('settlement_id','obligation_id','eligibility_decision_id','payment_authorization_id','environment','amount_cents','currency')
        approval={k:s[k] for k in fields}
        approval['expires_at']=(datetime.now(timezone.utc)+timedelta(minutes=5)).isoformat()
        approval.update(changes);os.environ['CANONICAL_CAPTURE_APPROVAL']=json.dumps(approval)
        os.environ['CANONICAL_LIVE_CAPTURE_ENABLED']='true'
    def ambiguous(self):
        s=self.settlement();self.gate(s);self.capture_failure=True
        r=capture_once(self.conn,settlement_id=s['settlement_id'],client=self.client)
        self.assertEqual(r.state,'OUTCOME_UNKNOWN');return s
    def test_explicit_environment_required(self):
        for v in (None,'live','TEST','production'):
            with self.subTest(v=v),self.assertRaises(ProviderBoundaryError):PayPalClient(v)
    def test_fixed_environment_and_no_relabel(self):
        self.assertEqual(self.client.endpoint,ENDPOINTS['PRODUCTION'])
        with self.assertRaises(ProviderBoundaryError):self.client._environment='SANDBOX'
    def test_no_generic_credential_fallback(self):
        with patch.dict(os.environ,{'PAYPAL_CLIENT_ID':'generic','PAYPAL_CLIENT_SECRET':'generic'},clear=True):
            with self.assertRaises(ProviderBoundaryError):self.client.get_order('ORDER1')
    def test_no_sandbox_credential_fallback(self):
        os.environ.pop('PAYPAL_PRODUCTION_CLIENT_ID')
        with self.assertRaises(ProviderBoundaryError):self.client.get_order('ORDER1')
    def test_copied_sandbox_credentials_rejected(self):
        os.environ['PAYPAL_PRODUCTION_CLIENT_ID']='fake-sandbox-id';os.environ['PAYPAL_PRODUCTION_CLIENT_SECRET']='fake-sandbox-secret'
        with self.assertRaises(ProviderBoundaryError):self.client.get_order('ORDER1')
    def test_wrong_provider_credentials_rejected(self):
        self.oauth_status=401
        with self.assertRaises(ProviderBoundaryError):self.client.get_order('ORDER1')
        self.assertFalse(any(x[0]=='GET' for x in self.calls))
    def test_sandbox_client_uses_sandbox_only(self):
        client=PayPalClient('SANDBOX');client.get_order('ORDER1')
        self.assertTrue(all('sandbox.paypal.com' in x[1] for x in self.calls))
    def test_sandbox_evidence_cannot_authorize_production(self):
        self.bind();self.authorization['links'][0]['href']='https://api-m.sandbox.paypal.com/v2/checkout/orders/ORDER1'
        receipt=PayPalClient('SANDBOX').observe_authorization('ORDER1','AUTH1')
        with self.assertRaises(ProviderBoundaryError):approve_payment_authorization(self.conn,obligation_id='OBL1',provider_evidence=receipt)
    def test_sandbox_link_cannot_satisfy_production(self):
        self.authorization['links'][0]['href']='https://api-m.sandbox.paypal.com/v2/checkout/orders/ORDER1'
        with self.assertRaises(ProviderBoundaryError):self.client.observe_authorization('ORDER1','AUTH1')
    def test_forged_authenticated_dictionary_rejected(self):
        with self.assertRaises(ProviderBoundaryError):approve_payment_authorization(self.conn,obligation_id='OBL1',provider_evidence={'authenticated':True,'environment':'PRODUCTION'})
    def test_evidence_constructor_rejected(self):
        with self.assertRaises(ProviderBoundaryError):ProviderEvidence(authenticated=True)
        with self.assertRaises(ProviderBoundaryError):evidence_data(object.__new__(ProviderEvidence))
    def test_legacy_production_self_assertion_rejected(self):
        with self.assertRaises(ValueError):establish_payment_authorization(environment='PRODUCTION',provider_event={'authenticated':True})
    def test_receipt_cannot_be_relabelled(self):
        receipt=self.client.observe_authorization('ORDER1','AUTH1');data=evidence_data(receipt);data['environment']='SANDBOX'
        self.assertEqual(evidence_data(receipt)['environment'],'PRODUCTION')
        with self.assertRaises(AttributeError):receipt.environment='SANDBOX'
    def test_exact_obligation_and_authorization(self):
        a=self.authorize();self.assertEqual((a['verified'],a['amount_cents'],a['currency'],a['environment']),(1,100,'AUD','PRODUCTION'))
        p=dict(self.conn.execute('SELECT * FROM payment_provider_evidence').fetchone())
        self.assertEqual((p['order_id'],p['provider_authorization_id'],p['payee_id']),('ORDER1','AUTH1','PAYEE1'))
        self.assertEqual(hashlib.sha256(p['observation_json'].encode()).hexdigest(),p['observation_sha256'])
    def test_missing_eligibility_cannot_register_obligation(self):
        fields={k:self.obligation[k] for k in ('obligation_id','task_id','eligibility_decision_id','contract_acceptance_decision_id','payee_id','amount_cents','currency')}
        fields.update(obligation_id='OBL2',eligibility_decision_id='missing')
        with self.assertRaises(ProviderBoundaryError):register_obligation(self.conn,**fields)
    def test_obligation_conflicting_replay_rejected(self):
        fields={k:self.obligation[k] for k in ('obligation_id','task_id','eligibility_decision_id','contract_acceptance_decision_id','payee_id','amount_cents','currency')}
        self.assertEqual(register_obligation(self.conn,**fields),self.obligation)
        fields['amount_cents']=101
        with self.assertRaises(ProviderBoundaryError):register_obligation(self.conn,**fields)
    def test_exact_authorization_replay(self):
        a=self.authorize();r=self.client.observe_authorization('ORDER1','AUTH1')
        b=approve_payment_authorization(self.conn,obligation_id='OBL1',provider_evidence=r);self.assertEqual(a,b)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM payment_provider_evidence').fetchone()[0],1)
    def test_exact_terms_mismatches_rejected(self):
        original=copy.deepcopy(self.order)
        for mutate in (
            lambda u:u.update(reference_id='OBL2'),
            lambda u:u['payee'].update(merchant_id='PAYEE2'),
            lambda u:u['amount'].update(value='2.00'),
            lambda u:u['amount'].update(currency_code='USD')):
            self.order=copy.deepcopy(original);mutate(self.order['purchase_units'][0])
            with self.subTest(order=self.order),self.assertRaises(ProviderBoundaryError):self.bind()
    def test_wrong_order_identity_rejected(self):
        self.order['id']='OTHER'
        with self.assertRaises(ProviderBoundaryError):self.bind()
    def test_wrong_authorization_relationship_rejected(self):
        self.order['purchase_units'][0]['payments']['authorizations'][0]['id']='OTHER'
        with self.assertRaises(ProviderBoundaryError):self.client.observe_authorization('ORDER1','AUTH1')
    def test_unapproved_or_multiple_authorizations_rejected(self):
        self.authorization['status']='VOIDED'
        with self.assertRaises(ProviderBoundaryError):self.client.observe_authorization('ORDER1','AUTH1')
        self.authorization['status']='CREATED';self.order['purchase_units'][0]['payments']['authorizations']*=2
        with self.assertRaises(ProviderBoundaryError):self.client.observe_authorization('ORDER1','AUTH1')
    def test_authorization_cannot_rebind_obligation(self):
        self.authorize();fields={k:self.obligation[k] for k in ('obligation_id','task_id','eligibility_decision_id','contract_acceptance_decision_id','payee_id','amount_cents','currency')}
        fields['obligation_id']='OBL2';register_obligation(self.conn,**fields)
        self.order['purchase_units'][0]['reference_id']='OBL2'
        with self.assertRaises(sqlite3.IntegrityError):bind_order(self.conn,self.client,'OBL2','ORDER1')
    def test_evidence_and_financial_terms_immutable(self):
        s=self.settlement()
        for table,assignment in (('payment_obligations',"amount_cents=101"),('payment_provider_evidence',"environment='SANDBOX'"),
                                  ('payment_authorizations',"verified=0"),('canonical_settlements',"amount_cents=101")):
            with self.subTest(table=table),self.assertRaises(sqlite3.IntegrityError):self.conn.execute('UPDATE '+table+' SET '+assignment)
    def test_request_ids_deterministic_distinct(self):
        self.assertEqual(request_identity('create','OBL1'),request_identity('create','OBL1'))
        self.assertNotEqual(request_identity('create','OBL1'),request_identity('authorize','OBL1'))
    def test_order_and_authorize_claim_before_dispatch_no_retry(self):
        create_provider_order(self.conn,self.client,'OBL1',approved_obligation_id='OBL1')
        authorize_provider_order(self.conn,self.client,'OBL1',approved_obligation_id='OBL1')
        self.assertEqual(self.conn.execute('SELECT count(*) FROM payment_operation_claims').fetchone()[0],2)
        with self.assertRaises(ProviderBoundaryError):create_provider_order(self.conn,self.client,'OBL1',approved_obligation_id='OBL1')
        with self.assertRaises(ProviderBoundaryError):authorize_provider_order(self.conn,self.client,'OBL1',approved_obligation_id='OBL1')
    def test_capture_default_off(self):
        s=self.settlement()
        with self.assertRaises(Exception):prepare_capture(self.conn,s['settlement_id'])
        self.assertEqual(self.dispatches,0)
    def test_exact_gate_success_and_claim_before_dispatch(self):
        s=self.settlement();self.gate(s);r=capture_once(self.conn,settlement_id=s['settlement_id'],client=self.client)
        self.assertEqual(r.state,'PROVIDER_CONFIRMED');self.assertEqual(self.dispatches,1)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM payment_capture_evidence').fetchone()[0],1)
        capture_call=next(x for x in self.calls if x[0]=='POST' and x[1].endswith('/capture'))
        self.assertEqual(capture_call[2]['json'],{'amount':{'value':'1.00','currency_code':'AUD'},'final_capture':True})
    def test_gate_mismatch_expiry_and_wrong_types(self):
        s=self.settlement()
        for changes in ({'settlement_id':'other'},{'obligation_id':'other'},{'amount_cents':True},
            {'expires_at':(datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat()},
            {'expires_at':(datetime.now(timezone.utc)+timedelta(hours=2)).isoformat()},
            {'expires_at':'invalid'}):
            self.gate(s,**changes)
            with self.subTest(changes=changes),self.assertRaises(ProviderBoundaryError):check_capture_approval(s)
    def test_unrelated_settlement_gate_rejected(self):
        s=self.settlement();self.gate(s)
        with self.assertRaises(ProviderBoundaryError):check_capture_approval({**s,'settlement_id':'unrelated'})
    def test_STOP_rejects_without_real_sentinel_mutation(self):
        s=self.settlement();self.gate(s)
        with patch('paypal_capture_service.Path.exists',return_value=True):
            with self.assertRaises(Exception):prepare_capture(self.conn,s['settlement_id'])
        self.assertEqual(self.dispatches,0)
    def test_gate_rechecked_after_claim(self):
        s=self.settlement();self.gate(s);prepare_capture(self.conn,s['settlement_id']);self.conn.commit()
        import paypal_capture_service as service
        original=service._claim_capture_attempt
        def revoke(*a):
            original(*a);os.environ.pop('CANONICAL_CAPTURE_APPROVAL')
        with patch.object(service,'_claim_capture_attempt',side_effect=revoke),self.assertRaises(ProviderBoundaryError):
            execute_capture(self.conn,s['settlement_id'],provider_client=self.client)
        self.assertEqual(self.dispatches,0)
    def test_production_caller_capture_function_rejected(self):
        s=self.settlement();self.gate(s);prepare_capture(self.conn,s['settlement_id']);self.conn.commit()
        with self.assertRaises(Exception):execute_capture(self.conn,s['settlement_id'],capture_fn=lambda *a:None,provider_client=self.client)
        with self.assertRaises(Exception):execute_capture(self.conn,s['settlement_id'],provider_client=PayPalClient('SANDBOX'))
        self.assertEqual(self.dispatches,0)
    def test_timeout_ambiguous_no_retry(self):
        s=self.ambiguous()
        with self.assertRaises(Exception):execute_capture(self.conn,s['settlement_id'],provider_client=self.client)
        self.assertEqual(self.dispatches,1)
    def test_restart_after_dispatch_crash_cannot_retry(self):
        s=self.settlement();self.gate(s);prepare_capture(self.conn,s['settlement_id']);self.conn.commit();self.capture_crash=True
        with self.assertRaises(KeyboardInterrupt):execute_capture(self.conn,s['settlement_id'],provider_client=self.client)
        with closing(sqlite3.connect(self.path)) as restarted:
            restarted.row_factory=sqlite3.Row
            with self.assertRaises(Exception):execute_capture(restarted,s['settlement_id'],provider_client=self.client)
        self.assertEqual(self.dispatches,1)
    def test_competing_capture_claim_rejected(self):
        s=self.settlement();self.gate(s);prepare_capture(self.conn,s['settlement_id']);self.conn.commit()
        import paypal_capture_service as service
        service._claim_capture_attempt(self.conn,s['settlement_id'],'claim1')
        with closing(sqlite3.connect(self.path)) as second:
            second.row_factory=sqlite3.Row
            with self.assertRaises(Exception):execute_capture(second,s['settlement_id'],provider_client=self.client)
        self.assertEqual(self.dispatches,0)
    def test_wrong_capture_terms_become_unknown(self):
        s=self.settlement();self.gate(s);self.capture['amount']['value']='2.00'
        r=capture_once(self.conn,settlement_id=s['settlement_id'],client=self.client);self.assertEqual(r.state,'OUTCOME_UNKNOWN')
        self.assertEqual(self.conn.execute('SELECT count(*) FROM payment_capture_evidence').fetchone()[0],0)
    def test_reconciliation_exact_resource_observation_only(self):
        s=self.ambiguous();self.capture_failure=False
        self.order['purchase_units'][0]['payments']['captures']=[self.capture]
        start=len(self.calls)
        r=reconcile_capture(self.conn,s['settlement_id'],provider_client=self.client);self.conn.commit()
        self.assertEqual(r.state,'PROVIDER_CONFIRMED')
        self.assertTrue(all(x[0] in ('TOKEN','GET') for x in self.calls[start:]));self.assertEqual(self.dispatches,1)
    def test_foreign_capture_cannot_resolve(self):
        s=self.ambiguous();self.capture['supplementary_data']['related_ids']['authorization_id']='OTHER'
        self.order['purchase_units'][0]['payments']['captures']=[self.capture]
        r=reconcile_capture(self.conn,s['settlement_id'],provider_client=self.client);self.conn.commit()
        self.assertEqual(r.state,'MANUAL_REVIEW_REQUIRED');self.assertEqual(self.dispatches,1)
    def test_reconciliation_NOT_CAPTURED_inconclusive(self):
        s=self.ambiguous();r=reconcile_capture(self.conn,s['settlement_id'],provider_client=self.client)
        self.assertEqual(r.state,'RECONCILING');self.conn.commit()
    def test_reconciliation_caller_assertion_rejected(self):
        s=self.ambiguous()
        with self.assertRaises(Exception):reconcile_capture(self.conn,s['settlement_id'],observe_fn=lambda *a:{'outcome':'COMPLETED','capture_id':'forged'},provider_client=self.client)
    def test_credentials_not_in_errors_or_records(self):
        self.oauth_status=401;output=io.StringIO()
        with redirect_stdout(output):
            try:self.client.get_order('ORDER1')
            except Exception as exc:output.write(str(exc))
        for secret in ('fake-prod-secret','fake-token','fake-sandbox-secret'):self.assertNotIn(secret,output.getvalue())
        self.oauth_status=200;self.authorize()
        dump=''.join(self.conn.iterdump())
        for secret in ('fake-prod-secret','fake-token','fake-sandbox-secret'):self.assertNotIn(secret,dump)
    def test_migration_idempotent_and_unregistered_seven(self):
        before=self.conn.execute('SELECT * FROM schema_meta').fetchall();apply_schema(self.conn)
        self.assertEqual(self.conn.execute('SELECT * FROM schema_meta').fetchall(),before)
        self.assertEqual([z[0] for z in before],[1,2,3,4,5,6,8,9,10])
    def test_historical_sandbox_rows_not_backfilled(self):
        # Build actual old schema privately, seed history, then apply only forward 009.
        old=Path(self.temp.name)/'old.db'
        import db
        from migration_008_settlement_eligibility import apply_schema as eight
        with bootstrap._database_path(old):
            db.init_db()
            import importlib
            for version,module,entry,name in bootstrap.MIGRATIONS:
                if version==9:break
                with redirect_stdout(io.StringIO()):getattr(importlib.import_module(module),entry)()
        with closing(sqlite3.connect(old)) as conn:
            conn.row_factory=sqlite3.Row;conn.execute('PRAGMA foreign_keys=ON')
            history=seed_chain(conn,environment='SANDBOX');conn.commit()
            before=[tuple(z) for z in conn.execute('SELECT * FROM external_acceptance_events')]
            apply_schema(conn);conn.commit()
            self.assertEqual([tuple(z) for z in conn.execute('SELECT * FROM external_acceptance_events')],before)
            self.assertEqual(conn.execute('SELECT count(*) FROM payment_provider_evidence').fetchone()[0],0)
            self.assertEqual(conn.execute('SELECT count(*) FROM payment_obligations').fetchone()[0],0)
    def test_private_authority_path_cannot_bypass_receipt(self):
        from payment_authority import _establish_payment_authorization
        with self.assertRaises(ValueError):
            _establish_payment_authorization(provider_event={'provider':'paypal','provider_event_id':'forged',
                'provider_authorization_id':'AUTH1','event_type':'authorization.established',
                'provider_status':'CREATED','amount_cents':100,'currency':'AUD','authenticated':True},
                task_id=self.chain['task_id'],contract_acceptance_decision_id=self.chain['contract_acceptance_decision_id'],
                expected_amount_cents=100,expected_currency='AUD',expected_provider='paypal',
                environment='PRODUCTION',_conn=self.conn)
    def test_conflicting_authorization_replay_rejected(self):
        self.authorize();self.authorization['id']='AUTH2'
        self.order['purchase_units'][0]['payments']['authorizations'][0]['id']='AUTH2'
        receipt=self.client.observe_authorization('ORDER1','AUTH2')
        with self.assertRaises(ProviderBoundaryError):
            approve_payment_authorization(self.conn,obligation_id='OBL1',provider_evidence=receipt)
    def test_capture_wrong_currency_relationship_and_payee(self):
        from production_payment_flow import validate_capture
        self.authorize();expected=dict(self.conn.execute('SELECT * FROM payment_provider_evidence').fetchone())
        original=copy.deepcopy(self.capture)
        mutations=(lambda x:x['amount'].update(currency_code='USD'),
                   lambda x:x['supplementary_data']['related_ids'].update(order_id='OTHER'),
                   lambda x:x.update(final_capture=False),
                   lambda x:x.update(payee={'merchant_id':'OTHER'}))
        for mutate in mutations:
            payload=copy.deepcopy(original);mutate(payload)
            self.capture=payload
            with self.subTest(payload=payload),self.assertRaises(ProviderBoundaryError):validate_capture(self.client.get_capture('CAP1'),expected)
    def test_customer_approval_link_environment_bound(self):
        self.order['links']=[{'rel':'approve','href':'https://www.paypal.com/checkoutnow?token=ORDER1'}]
        self.assertEqual(self.client.get_approval_url('ORDER1'),self.order['links'][0]['href'])
        self.order['links'][0]['href']='https://www.sandbox.paypal.com/checkoutnow?token=ORDER1'
        with self.assertRaises(ProviderBoundaryError):self.client.get_approval_url('ORDER1')
    def test_capture_gate_missing_or_extra_fields_rejected(self):
        s=self.settlement();os.environ['CANONICAL_LIVE_CAPTURE_ENABLED']='true'
        with self.assertRaises(ProviderBoundaryError):check_capture_approval(s)
        self.gate(s,unexpected=True)
        with self.assertRaises(ProviderBoundaryError):check_capture_approval(s)
    def test_missing_order_binding_cannot_authorize(self):
        r=self.client.observe_authorization('ORDER1','AUTH1')
        with self.assertRaises(ProviderBoundaryError):approve_payment_authorization(self.conn,obligation_id='OBL1',provider_evidence=r)
    def test_transaction_rollback_removes_all_authorization_facts(self):
        self.bind();r=self.client.observe_authorization('ORDER1','AUTH1')
        self.conn.execute('BEGIN IMMEDIATE')
        approve_payment_authorization(self.conn,obligation_id='OBL1',provider_evidence=r)
        self.conn.rollback()
        for table in ('payment_authorizations','payment_provider_evidence','payment_authorization_events','payment_authorization_verifications'):
            self.assertEqual(self.conn.execute('SELECT count(*) FROM '+table).fetchone()[0],0)
    def test_provider_redirect_rejected_without_following(self):
        self.oauth_status=302
        with self.assertRaises(ProviderBoundaryError):self.client.get_order('ORDER1')
        self.assertEqual(len(self.calls),1)
    def test_reconciliation_wrong_terms_require_manual_review(self):
        s=self.ambiguous();self.order['purchase_units'][0]['payee']['merchant_id']='OTHER'
        r=reconcile_capture(self.conn,s['settlement_id'],provider_client=self.client)
        self.assertEqual(r.state,'MANUAL_REVIEW_REQUIRED');self.assertEqual(self.dispatches,1)
    def test_settlement_wrong_obligation_rejected(self):
        auth=self.authorize()
        with self.assertRaises(ProviderBoundaryError):
            create_settlement(self.conn,task_id=self.chain['task_id'],obligation_id='OTHER',
                contract_acceptance_decision_id=self.chain['contract_acceptance_decision_id'],
                payment_authorization_id=auth['authorization_id'],amount_cents=100,currency='AUD',
                provider='paypal',environment='PRODUCTION',eligibility_decision_id=self.eligibility['decision_id'])
    def test_unique_order_replace_cannot_delete_previous_binding(self):
        self.bind()
        fields={k:self.obligation[k] for k in ('obligation_id','task_id','eligibility_decision_id','contract_acceptance_decision_id','payee_id','amount_cents','currency')}
        fields['obligation_id']='OBL2';register_obligation(self.conn,**fields)
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("INSERT OR REPLACE INTO payment_order_bindings VALUES('OBL2','ORDER1','different-create','different-auth','now')")
        self.assertEqual(self.conn.execute('SELECT obligation_id FROM payment_order_bindings').fetchone()[0],'OBL1')
    def test_unique_evidence_replace_cannot_remove_provenance(self):
        self.authorize()
        old=dict(self.conn.execute('SELECT * FROM payment_provider_evidence').fetchone())
        new={**old,'evidence_id':'different'}
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("INSERT OR REPLACE INTO payment_provider_evidence ("+','.join(new)+") VALUES ("+','.join(':'+k for k in new)+")",new)
        self.assertEqual(dict(self.conn.execute('SELECT * FROM payment_provider_evidence').fetchone()),old)
    def test_unique_authorization_replace_cannot_remove_binding(self):
        self.authorize()
        old=dict(self.conn.execute('SELECT * FROM payment_authorizations').fetchone())
        new={**old,'authorization_id':'different'}
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("INSERT OR REPLACE INTO payment_authorizations ("+','.join(new)+") VALUES ("+','.join(':'+k for k in new)+")",new)
        self.assertEqual(dict(self.conn.execute('SELECT * FROM payment_authorizations').fetchone()),old)
    def test_manual_production_confirmation_cannot_bypass_evidence(self):
        s=self.settlement();self.gate(s);prepare_capture(self.conn,s['settlement_id']);self.conn.commit()
        with self.assertRaises(ProviderBoundaryError):
            transition_settlement(self.conn,s['settlement_id'],'PROVIDER_CONFIRMED',reason='forged',provider_capture_id='forged')
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("UPDATE canonical_settlements SET state='PROVIDER_CONFIRMED',provider_capture_id='forged' WHERE settlement_id=?",(s['settlement_id'],))
    def test_confirmed_capture_identity_immutable(self):
        s=self.settlement();self.gate(s);capture_once(self.conn,settlement_id=s['settlement_id'],client=self.client)
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("UPDATE canonical_settlements SET provider_capture_id='OTHER' WHERE settlement_id=?",(s['settlement_id'],))
    def test_production_revenue_requires_exact_capture_proof(self):
        from canonical_settlement_engine import record_revenue
        s=self.settlement()
        with self.assertRaises(ProviderBoundaryError):
            record_revenue(self.conn,s['settlement_id'],ledger_id='synthetic-ledger',reason='test')
        self.gate(s);capture_once(self.conn,settlement_id=s['settlement_id'],client=self.client)
        result=record_revenue(self.conn,s['settlement_id'],ledger_id='synthetic-ledger',reason='synthetic test')
        self.assertEqual(result['state'],'REVENUE_RECORDED');self.conn.commit()
        self.assertEqual(record_revenue(self.conn,s['settlement_id'],ledger_id='synthetic-ledger',reason='replay'),result)
    def test_direct_production_client_capture_requires_permit(self):
        with self.assertRaises(ProviderBoundaryError):
            self.client.capture_authorization('AUTH1',request_id='request1',amount_cents=100,currency='AUD')
        self.assertEqual(self.calls,[])
    def test_permit_is_single_use_after_committed_claim(self):
        from production_payment_flow import committed_capture_permit
        import paypal_capture_service as service
        s=self.settlement();self.gate(s);prepare_capture(self.conn,s['settlement_id']);self.conn.commit()
        request_id=service.build_capture_request_id(s['settlement_id'])
        with self.assertRaises(ProviderBoundaryError):committed_capture_permit(self.conn,s,request_id)
        receipt=service._claim_capture_attempt(self.conn,s['settlement_id'],request_id)
        permit=committed_capture_permit(self.conn,s,request_id,claim_receipt=receipt)
        self.client.capture_authorization('AUTH1',request_id=request_id,amount_cents=100,currency='AUD',permit=permit)
        with self.assertRaises(ProviderBoundaryError):
            self.client.capture_authorization('AUTH1',request_id=request_id,amount_cents=100,currency='AUD',permit=permit)
        self.assertEqual(self.dispatches,1)
    def test_gate_rechecked_after_oauth_before_dispatch(self):
        s=self.settlement();self.gate(s)
        original=FakeSession.post
        def revoke(session,*args,**kwargs):
            response=original(session,*args,**kwargs)
            if args[0].endswith('/v1/oauth2/token'):os.environ.pop('CANONICAL_CAPTURE_APPROVAL',None)
            return response
        with patch.object(FakeSession,'post',revoke):
            r=capture_once(self.conn,settlement_id=s['settlement_id'],client=self.client)
        self.assertEqual(r.state,'OUTCOME_UNKNOWN');self.assertEqual(self.dispatches,0)
    def test_existing_claim_cannot_issue_fresh_permit_after_restart(self):
        from production_payment_flow import committed_capture_permit
        import paypal_capture_service as service
        s=self.settlement();self.gate(s);prepare_capture(self.conn,s['settlement_id']);self.conn.commit()
        request_id=service.build_capture_request_id(s['settlement_id'])
        receipt=service._claim_capture_attempt(self.conn,s['settlement_id'],request_id)
        committed_capture_permit(self.conn,s,request_id,claim_receipt=receipt)
        for proof in (None,receipt):
            with self.assertRaises(ProviderBoundaryError):
                committed_capture_permit(self.conn,s,request_id,claim_receipt=proof)
        self.assertEqual(self.dispatches,0)
    def test_original_fabricated_capture_revenue_sequence_rejected(self):
        from production_payment_flow import validate_capture,preserve_capture
        from canonical_settlement_engine import record_revenue
        s=self.settlement()
        expected=dict(self.conn.execute('SELECT * FROM payment_provider_evidence').fetchone())
        with self.assertRaises(ProviderBoundaryError):validate_capture(dict(self.capture),expected)
        shaped={'capture_id':'CAP1','authorization_id':'AUTH1','order_id':'ORDER1','payee_id':'PAYEE1',
            'amount_cents':100,'currency':'AUD','environment':'PRODUCTION','endpoint':'https://api-m.paypal.com',
            'status':'COMPLETED','final_capture':True,'observed_at':'now','provenance_source':'capture_response'}
        with self.assertRaises(ProviderBoundaryError):preserve_capture(self.conn,s,shaped)
        transition_settlement(self.conn,s['settlement_id'],'CAPTURE_REQUESTED',reason='synthetic adversary')
        with self.assertRaises(ProviderBoundaryError):
            transition_settlement(self.conn,s['settlement_id'],'PROVIDER_CONFIRMED',reason='forged',provider_capture_id='CAP1')
        with self.assertRaises(ProviderBoundaryError):
            record_revenue(self.conn,s['settlement_id'],ledger_id='adversarial-ledger',reason='forged')
        self.assertEqual(self.conn.execute('SELECT count(*) FROM payment_capture_evidence').fetchone()[0],0)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM revenue_ledger').fetchone()[0],0)
        self.assertEqual(self.dispatches,0)
    def test_capture_receipt_constructor_and_mutation_rejected(self):
        from paypal_client import CaptureEvidence,capture_evidence_data
        with self.assertRaises(ProviderBoundaryError):CaptureEvidence(authenticated=True)
        with self.assertRaises(ProviderBoundaryError):capture_evidence_data(object.__new__(CaptureEvidence))
        receipt=self.client.get_capture('CAP1');copy=capture_evidence_data(receipt)
        copy['environment']='SANDBOX'
        self.assertEqual(capture_evidence_data(receipt)['environment'],'PRODUCTION')
        with self.assertRaises(AttributeError):receipt.environment='SANDBOX'
    def test_sandbox_capture_resource_cannot_satisfy_production(self):
        from production_payment_flow import preserve_capture
        s=self.settlement()
        raw=PayPalClient('SANDBOX').get_capture('CAP1')
        with self.assertRaises(ProviderBoundaryError):preserve_capture(self.conn,s,raw)
    def test_valid_capture_receipt_persists_provenance_and_proof(self):
        s=self.settlement();self.gate(s)
        capture_once(self.conn,settlement_id=s['settlement_id'],client=self.client)
        row=dict(self.conn.execute('SELECT * FROM payment_capture_evidence').fetchone())
        self.assertEqual(row['provenance_source'],'capture_response')
        data=json.loads(row['observation_json'])
        self.assertEqual(data['provenance_source'],row['provenance_source'])
        self.assertEqual(hashlib.sha256(row['observation_json'].encode()).hexdigest(),row['observation_sha256'])
        self.assertEqual((data['authorization_id'],data['order_id'],data['payee_id']),('AUTH1','ORDER1','PAYEE1'))
    def test_claim_delete_update_replace_and_upsert_rejected(self):
        import paypal_capture_service as service
        s=self.settlement();self.gate(s);prepare_capture(self.conn,s['settlement_id']);self.conn.commit()
        request_id=service.build_capture_request_id(s['settlement_id'])
        service._claim_capture_attempt(self.conn,s['settlement_id'],request_id)
        statements=(
            ("DELETE FROM canonical_capture_attempts WHERE settlement_id=?",(s['settlement_id'],)),
            ("UPDATE canonical_capture_attempts SET request_id='replacement' WHERE settlement_id=?",(s['settlement_id'],)),
            ("UPDATE canonical_capture_attempts SET settlement_id='replacement' WHERE settlement_id=?",(s['settlement_id'],)),
            ("UPDATE canonical_capture_attempts SET claimed_at='replacement' WHERE settlement_id=?",(s['settlement_id'],)),
            ("INSERT OR REPLACE INTO canonical_capture_attempts(settlement_id,request_id) VALUES(?,?)",(s['settlement_id'],'replacement')),
            ("INSERT OR REPLACE INTO canonical_capture_attempts(settlement_id,request_id) VALUES(?,?)",('replacement',request_id)),
            ("INSERT INTO canonical_capture_attempts(settlement_id,request_id) VALUES(?,?) ON CONFLICT(settlement_id) DO UPDATE SET request_id='replacement'",(s['settlement_id'],request_id)),
            ("INSERT OR IGNORE INTO canonical_capture_attempts(settlement_id,request_id) VALUES(?,?)",(s['settlement_id'],request_id)))
        for sql,params in statements:
            with self.subTest(sql=sql),self.assertRaises(sqlite3.IntegrityError):self.conn.execute(sql,params)
        self.conn.rollback()
        self.assertEqual(self.conn.execute('SELECT request_id FROM canonical_capture_attempts').fetchone()[0],request_id)
    def test_original_claim_delete_restart_sequence_blocked(self):
        s=self.settlement();self.gate(s);prepare_capture(self.conn,s['settlement_id']);self.conn.commit()
        self.capture_crash=True
        with self.assertRaises(KeyboardInterrupt):execute_capture(self.conn,s['settlement_id'],provider_client=self.client)
        with self.assertRaises(sqlite3.IntegrityError):self.conn.execute('DELETE FROM canonical_capture_attempts')
        self.conn.rollback();self.capture_crash=False
        with closing(sqlite3.connect(self.path)) as restarted:
            restarted.row_factory=sqlite3.Row;restarted.execute('PRAGMA foreign_keys=ON')
            with self.assertRaises(Exception):execute_capture(restarted,s['settlement_id'],provider_client=self.client)
        self.assertEqual(self.dispatches,1)
    def test_temporary_oauth_failure_stays_reconcilable_then_resolves(self):
        s=self.ambiguous();self.oauth_status=401
        r=reconcile_capture(self.conn,s['settlement_id'],provider_client=self.client);self.conn.commit()
        self.assertEqual(r.state,'RECONCILING')
        self.oauth_status=200;self.capture_failure=False
        self.order['purchase_units'][0]['payments']['captures']=[self.capture]
        start=len(self.calls)
        r=reconcile_capture(self.conn,s['settlement_id'],provider_client=self.client);self.conn.commit()
        self.assertEqual(r.state,'PROVIDER_CONFIRMED');self.assertEqual(self.dispatches,1)
        self.assertTrue(all(x[0] in ('TOKEN','GET') for x in self.calls[start:]))
        self.assertEqual(self.conn.execute('SELECT provenance_source FROM payment_capture_evidence').fetchone()[0],'capture_retrieval')
    def test_temporary_network_read_failure_stays_reconcilable(self):
        import requests
        s=self.ambiguous();original=FakeSession.request
        def timeout(session,method,url,**kwargs):
            if method=='GET':raise requests.exceptions.Timeout('fake-prod-secret fake-token')
            return original(session,method,url,**kwargs)
        with patch.object(FakeSession,'request',timeout):
            r=reconcile_capture(self.conn,s['settlement_id'],provider_client=self.client);self.conn.commit()
        self.assertEqual(r.state,'RECONCILING');self.assertEqual(self.dispatches,1)
        self.order['purchase_units'][0]['payments']['captures']=[self.capture]
        self.assertEqual(reconcile_capture(self.conn,s['settlement_id'],provider_client=self.client).state,'PROVIDER_CONFIRMED')
        self.assertEqual(self.dispatches,1)
    def test_temporary_provider_read_error_does_not_become_manual(self):
        s=self.ambiguous();original=FakeSession.request
        def unavailable(session,method,url,**kwargs):
            if method=='GET':return Response({'message':'unavailable'},503)
            return original(session,method,url,**kwargs)
        with patch.object(FakeSession,'request',unavailable):
            r=reconcile_capture(self.conn,s['settlement_id'],provider_client=self.client)
        self.assertEqual(r.state,'RECONCILING');self.assertEqual(self.dispatches,1)
    def test_malformed_authoritative_capture_still_fails_closed(self):
        s=self.ambiguous();self.order['purchase_units'][0]['payments']['captures']=[self.capture]
        self.capture['amount']={}
        r=reconcile_capture(self.conn,s['settlement_id'],provider_client=self.client)
        self.assertEqual(r.state,'MANUAL_REVIEW_REQUIRED')
        self.assertEqual(self.conn.execute('SELECT count(*) FROM payment_capture_evidence').fetchone()[0],0)
    def schema_verifier(self,path,*extra):
        import subprocess,sys
        root=Path(__file__).resolve().parent
        return subprocess.run([sys.executable,'-B',str(root/'verify_production_candidate.py'),
                               '--db',str(path),'--schema-only',*extra],capture_output=True,text=True)
    def test_verifier_accepts_intact_supplied_schema_readonly(self):
        self.conn.commit();before=self.path.read_bytes()
        result=self.schema_verifier(self.path)
        self.assertEqual(result.returncode,0,result.stdout)
        self.assertIn('supplied_database_schema: current_structurally_valid',result.stdout)
        self.assertEqual(self.path.read_bytes(),before)
    def test_original_verifier_missing_guard_sequence_fails(self):
        self.conn.commit()
        for guard in ('production_capture_confirmation_guard','production_capture_evidence_binding',
                      'production_capture_claim_no_delete','production_settlement_payment_binding'):
            damaged=Path(self.temp.name)/(guard+'.db')
            with closing(sqlite3.connect(damaged)) as copy:
                self.conn.backup(copy);copy.execute('DROP TRIGGER '+guard);copy.commit()
            before=damaged.read_bytes();result=self.schema_verifier(damaged)
            self.assertNotEqual(result.returncode,0,result.stdout)
            self.assertIn('supplied_database_schema: FAILED',result.stdout)
            self.assertEqual(damaged.read_bytes(),before)
            self.assertNotEqual(self.schema_verifier(damaged,'--migration-compatibility').returncode,0)
    def test_verifier_detects_missing_table_and_index(self):
        self.conn.commit()
        for statement in ('DROP TABLE payment_operation_claims','DROP INDEX ux_canonical_provider_capture'):
            damaged=Path(self.temp.name)/('table.db' if 'TABLE' in statement else 'index.db')
            with closing(sqlite3.connect(damaged)) as copy:
                self.conn.backup(copy);copy.execute(statement);copy.commit()
            self.assertNotEqual(self.schema_verifier(damaged).returncode,0)
    def test_verifier_compatibility_is_not_actual_readiness(self):
        self.conn.commit()
        # Drop all 009 objects in a private fixture to represent the frozen older DB.
        old=Path(self.temp.name)/'older.db'
        with closing(sqlite3.connect(old)) as copy:
            self.conn.backup(copy)
            for row in copy.execute("SELECT name FROM sqlite_master WHERE type='trigger'").fetchall():
                if row[0].startswith(('payment_boundary_','production_')) or row[0] in ('payment_obligation_eligibility','payment_provider_binding'):
                    copy.execute('DROP TRIGGER '+row[0])
            for table in ('payment_capture_evidence','payment_operation_claims','payment_provider_evidence','payment_order_bindings','payment_obligations'):
                copy.execute('DROP TABLE '+table)
            for (name,) in copy.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'forward_evidence_%'").fetchall():
                copy.execute('DROP TRIGGER '+name)
            copy.execute('DELETE FROM schema_meta WHERE version IN (9,10)');copy.commit()
        before=old.read_bytes()
        self.assertNotEqual(self.schema_verifier(old).returncode,0)
        compatibility=self.schema_verifier(old,'--migration-compatibility')
        self.assertEqual(compatibility.returncode,0,compatibility.stdout)
        self.assertIn('supplied_database_readiness: NOT_ESTABLISHED',compatibility.stdout)
        self.assertEqual(old.read_bytes(),before)
    def test_request_conflict_replace_with_valid_other_settlement_rejected(self):
        from eligibility_test_support import seed_authorization
        import paypal_capture_service as service
        s=self.settlement();self.gate(s);prepare_capture(self.conn,s['settlement_id']);self.conn.commit()
        request_id=service.build_capture_request_id(s['settlement_id'])
        service._claim_capture_attempt(self.conn,s['settlement_id'],request_id)
        other=seed_chain(self.conn,environment='TEST')
        authorization=seed_authorization(self.conn,other)
        eligibility=approve_settlement_eligibility(self.conn,**other)
        alternate=create_settlement(self.conn,task_id=other['task_id'],obligation_id='alternate-claim-obligation',
            contract_acceptance_decision_id=other['contract_acceptance_decision_id'],
            payment_authorization_id=authorization,amount_cents=100,currency='AUD',provider='TEST_PROVIDER',
            environment='TEST',eligibility_decision_id=eligibility['decision_id'])
        self.conn.commit()
        with self.assertRaisesRegex(sqlite3.IntegrityError,'replacement forbidden'):
            self.conn.execute('INSERT OR REPLACE INTO canonical_capture_attempts(settlement_id,request_id) VALUES(?,?)',
                              (alternate['settlement_id'],request_id))
        self.assertEqual(self.conn.execute('SELECT settlement_id FROM canonical_capture_attempts').fetchone()[0],s['settlement_id'])
    def test_integrity_foreign_keys(self):
        self.settlement()
        self.assertEqual(self.conn.execute('PRAGMA integrity_check').fetchone()[0],'ok')
        self.assertEqual(self.conn.execute('PRAGMA foreign_key_check').fetchall(),[])

if __name__=='__main__':unittest.main(argv=['production-payment-boundary'],exit=True)
