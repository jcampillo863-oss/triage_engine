import sqlite3
import tempfile
import unittest
from pathlib import Path
from test_paypal_capture_service import make_test_db, seed_payment_authorized_settlement
from paypal_capture_service import prepare_capture, execute_capture, CaptureServiceError
from canonical_settlement_engine import transition_settlement

class CaptureSafetyTests(unittest.TestCase):
    def fixture(self):
        c=make_test_db()
        seed_payment_authorized_settlement(c)
        prepare_capture(c,'settlement-test-001')
        c.commit()
        return c

    def test_claim_durable_before_provider_contact(self):
        c=self.fixture()
        def provider(*args,**kwargs):
            self.assertFalse(c.in_transaction)
            self.assertEqual(c.execute('SELECT count(*) FROM canonical_capture_attempts').fetchone()[0],1)
            return {'id':'capture-unit-test','status':'COMPLETED'}
        self.assertEqual(execute_capture(c,'settlement-test-001',capture_fn=provider).state,'PROVIDER_CONFIRMED')
        c.close()

    def test_preupgrade_prepared_capture_quarantined(self):
        c=self.fixture()
        c.execute('DROP TABLE canonical_capture_attempts')
        c.commit()
        calls=[]
        with self.assertRaises(CaptureServiceError):
            execute_capture(c,'settlement-test-001',capture_fn=lambda *a,**kw:calls.append(1))
        self.assertEqual(calls,[])
        self.assertEqual(c.execute('SELECT count(*) FROM canonical_capture_attempts').fetchone()[0],1)
        c.close()

    def test_crash_restart_never_calls_provider_twice(self):
        c=self.fixture()
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'capture.db'
            disk=sqlite3.connect(path)
            c.backup(disk)
            c.close()
            disk.row_factory=sqlite3.Row
            calls=[]
            def crash(*args,**kwargs):
                calls.append(1)
                raise KeyboardInterrupt('simulated process loss')
            with self.assertRaises(KeyboardInterrupt):
                execute_capture(disk,'settlement-test-001',capture_fn=crash)
            disk.close()
            restarted=sqlite3.connect(path)
            restarted.row_factory=sqlite3.Row
            with self.assertRaises(CaptureServiceError):
                execute_capture(restarted,'settlement-test-001',capture_fn=crash)
            self.assertEqual(len(calls),1)
            restarted.close()

    def test_unknown_response_never_blind_retried(self):
        for response in ({},None,{'id':'capture-unit-test','status':'PENDING'}):
            c=self.fixture()
            calls=[]
            def provider(*a,**kw):
                calls.append(1)
                return response
            result=execute_capture(c,'settlement-test-001',capture_fn=provider)
            self.assertEqual(result.state,'OUTCOME_UNKNOWN')
            c.rollback() # Even if the caller loses the outcome write, the claim survives.
            with self.assertRaises(CaptureServiceError):
                execute_capture(c,'settlement-test-001',capture_fn=provider)
            self.assertEqual(len(calls),1)
            c.close()

    def test_second_process_connection_cannot_capture_during_first_call(self):
        c=self.fixture()
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'capture.db'
            first=sqlite3.connect(path)
            c.backup(first)
            c.close()
            first.row_factory=sqlite3.Row
            second=sqlite3.connect(path)
            second.row_factory=sqlite3.Row
            calls=[]
            def provider(*a,**kw):
                calls.append(1)
                with self.assertRaises(CaptureServiceError):
                    execute_capture(second,'settlement-test-001',capture_fn=provider)
                return {'id':'one-capture','status':'COMPLETED'}
            self.assertEqual(execute_capture(first,'settlement-test-001',capture_fn=provider).state,'PROVIDER_CONFIRMED')
            self.assertEqual(len(calls),1)
            first.close()
            second.close()

if __name__=='__main__':
    unittest.main(argv=['capture-safety'],exit=True)
