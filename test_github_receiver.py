import hashlib
import hmac
import json
import unittest
import uuid
from unittest.mock import patch
from pathlib import Path

import db
from work_delivery import create_delivery
from github_webhook_receiver import create_app
from paypal_capture_service import _check_capture_gate, CaptureServiceError

class ReceiverTests(unittest.TestCase):
    def setUp(self):
        self.config = dict(TESTING=True, GITHUB_REPOSITORY='example-owner/example-repo',
            GITHUB_WEBHOOK_SECRET='unit-test-secret', ACCEPTANCE_ENVIRONMENT='TEST', GITHUB_ACCEPTANCE_ENABLED=True)
        self.app = create_app(self.config)
        self.client = self.app.test_client()
        self.delivery = str(uuid.uuid4())
        self.pr = 987654
        self.task = '__receiver_test__'
        self.payload = {'action':'closed', 'repository':{'full_name':self.config['GITHUB_REPOSITORY']},
            'pull_request':{'number':self.pr,'html_url':f'https://github.com/example-owner/example-repo/pull/{self.pr}',
                'merged':True, 'merge_commit_sha':'a'*40}}
        create_delivery(task_id=self.task, repository=self.config['GITHUB_REPOSITORY'], pull_request_number=self.pr,
            pull_request_url=self.payload['pull_request']['html_url'], environment='TEST')

    def post(self, payload=None, raw=None, signature=None, event='pull_request', client=None):
        raw = raw if raw is not None else json.dumps(payload or self.payload).encode()
        sig = signature if signature is not None else 'sha256=' + hmac.new(b'unit-test-secret',raw,hashlib.sha256).hexdigest()
        return (client or self.client).post('/webhook/github', data=raw, headers={
            'X-Hub-Signature-256':sig, 'X-GitHub-Delivery':self.delivery, 'X-GitHub-Event':event})

    def financial(self):
        with db.get_db() as c:
            return {t:[tuple(r) for r in c.execute(f'SELECT * FROM {t} ORDER BY rowid')] for t in
                ('canonical_settlements','canonical_settlement_journal','payment_authorizations','provider_events','revenue_ledger')}

    def test_signed_binding_replay_restart_and_financial_boundary(self):
        before = self.financial()
        first = self.post()
        self.assertEqual(first.status_code,200)
        self.assertTrue(first.json['accepted'])
        again = self.post(client=create_app(self.config).test_client())
        self.assertEqual(again.json,first.json)
        with db.get_db() as c:
            rows = c.execute('SELECT * FROM external_acceptance_events WHERE external_event_id=?',(self.delivery,)).fetchall()
            self.assertEqual(len(rows),1)
            self.assertEqual(rows[0]['task_id'],self.task)
            raw = json.loads(rows[0]['raw_payload'])
            self.assertEqual(json.loads(raw['github_raw_body']), self.payload)
            self.assertEqual(raw['github_raw_body_sha256'],hashlib.sha256(raw['github_raw_body'].encode()).hexdigest())
        self.assertEqual(before,self.financial())

    def test_invalid_signature_before_parse(self):
        self.assertEqual(self.post(raw=b'not-json',signature='sha256='+'0'*64).status_code,401)

    def test_authenticated_malformed_payload(self):
        for raw in (b'not-json',b'[]',b'{"repository":5}',b'{"repository":{"full_name":"example-owner/example-repo"},"action":"closed","pull_request":5}'):
            self.assertEqual(self.post(raw=raw).status_code,400)

    def test_wrong_repository(self):
        self.payload['repository']['full_name']='wrong/repo'
        self.assertEqual(self.post().status_code,400)

    def test_unknown_pr(self):
        self.payload['pull_request']['number']=876543
        self.assertEqual(self.post().status_code,409)

    def test_conflicting_raw_replay(self):
        self.assertEqual(self.post().status_code,200)
        self.payload['pull_request']['merge_commit_sha']='b'*40
        self.assertEqual(self.post().status_code,409)

    def test_external_task_id_not_trusted(self):
        self.payload['task_id']='attacker-task'
        response=self.post()
        self.assertEqual(response.status_code,200)
        with db.get_db() as c:
            self.assertEqual(c.execute('SELECT task_id FROM external_acceptance_events WHERE external_event_id=?',(self.delivery,)).fetchone()[0],self.task)

    def test_unmerged_not_positive(self):
        self.payload['pull_request']['merged']=False
        response=self.post()
        self.assertEqual(response.status_code,200)
        self.assertFalse(response.json['accepted'])

    def test_disabled_missing_config_and_kill(self):
        for changes in ({'GITHUB_ACCEPTANCE_ENABLED':False},{'GITHUB_WEBHOOK_SECRET':''},{'ACCEPTANCE_ENVIRONMENT':''},{'GITHUB_REPOSITORY':''}):
            client=create_app({**self.config,**changes}).test_client()
            self.assertEqual(client.get('/health').status_code,503)
            self.assertEqual(self.post(client=client).status_code,503)
        with patch('github_webhook_receiver.STOP_FILE') as stop:
            stop.exists.return_value=True
            self.assertEqual(self.post().status_code,503)

    def test_limits_and_unsupported_event(self):
        self.assertEqual(self.post(event='push').status_code,400)
        self.assertEqual(self.post(raw=b'x'*(1024*1024+1)).status_code,413)

    def test_live_gate_default_off(self):
        with patch.dict('os.environ',{},clear=True):
            with self.assertRaises(CaptureServiceError):
                _check_capture_gate({'environment':'PRODUCTION'})
        with patch('paypal_capture_service.Path') as path:
            path.return_value.resolve.return_value.parent.__truediv__.return_value.__truediv__.return_value.exists.return_value=True
            with self.assertRaises(CaptureServiceError):
                _check_capture_gate({'environment':'SANDBOX'})

    def test_receiver_has_no_financial_imports(self):
        import ast
        import github_webhook_receiver
        tree=ast.parse(Path(github_webhook_receiver.__file__).read_text())
        imports=[n.module or '' for n in ast.walk(tree) if isinstance(n,ast.ImportFrom)]
        imports += [a.name for n in ast.walk(tree) if isinstance(n,ast.Import) for a in n.names]
        self.assertFalse(any(any(word in name for word in ('paypal','payment','settlement','revenue','payout')) for name in imports))

if __name__=='__main__':
    unittest.main(argv=['receiver-tests'],exit=True)
