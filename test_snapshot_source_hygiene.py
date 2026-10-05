"""Offline checks for manual gates and retained static reproducibility assets."""
import ast
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parent

class SnapshotHygieneTests(unittest.TestCase):
    def tree(self,name):return ast.parse((ROOT/name).read_text(encoding='utf-8-sig'))

    def test_manual_genuine_capture_has_no_historical_defaults(self):
        tree=self.tree('test_paypal_genuine_capture.py')
        constants=[t.id for n in tree.body if isinstance(n,ast.Assign) for t in n.targets if isinstance(t,ast.Name)]
        self.assertNotIn('SETTLEMENT_ID',constants)
        self.assertNotIn('EXPECTED_AMOUNT_CENTS',constants)
        self.assertNotIn('EXPECTED_CURRENCY',constants)
        main=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='main')
        self.assertEqual([a.arg for a in main.args.kwonlyargs],['settlement_id','expected_amount_cents','expected_currency','manual_confirmation'])
        self.assertEqual(main.args.kw_defaults[:3],[None,None,None])
        self.assertFalse(main.args.kw_defaults[3].value)
        self.assertIsInstance(main.body[0],ast.If)
        self.assertTrue(any(isinstance(n,ast.Raise) for n in ast.walk(main.body[0])))
        connect_calls=[n for n in ast.walk(main) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='connect']
        self.assertTrue(connect_calls)
        self.assertLess(main.body[0].lineno,connect_calls[0].lineno)
        self.assertTrue(any(isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='__test__' for t in n.targets)
            and isinstance(n.value,ast.Constant) and n.value.value is False for n in tree.body))

    def test_retired_direct_capture_raises_before_provider_code(self):
        tree=self.tree('test_paypal_capture_order.py')
        capture=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='capture_order')
        self.assertIsInstance(capture.body[0],ast.Raise)
        for n in ast.walk(capture):
            if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='order_id' for t in n.targets):
                self.assertIsInstance(n.value,ast.Constant);self.assertIsNone(n.value.value)

    def test_canonical_integration_has_no_provider_client(self):
        tree=self.tree('test_paypal_canonical_integration.py')
        imports=[n.module or '' for n in ast.walk(tree) if isinstance(n,ast.ImportFrom)]
        imports.extend(a.name for n in ast.walk(tree) if isinstance(n,ast.Import) for a in n.names)
        self.assertNotIn('paypal_sandbox_client',imports)
        self.assertNotIn('requests',imports)
        self.assertNotIn('urllib.request',imports)
        self.assertFalse(any(isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='ORDER_ID' for t in n.targets) for n in tree.body))

    def test_existing_retirement_guards_remain(self):
        for name in ('settlement_webhook.py','settlement_worker.py','webhook_listener.py','paypal_payouts.py'):
            self.assertIsInstance(self.tree(name).body[0],ast.Raise)
        tree=self.tree('dashboard.py')
        method=next(n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name=='trigger_paypal_settlement')
        self.assertIsInstance(method.body[0],ast.Raise)

    def test_retained_legacy_pair_is_reproducible_without_workspace_writes(self):
        module=types.ModuleType('sfloadmacro')
        source=ROOT/'workspace/task_heavy_task_1500/sfloadmacro.py'
        exec(compile(source.read_text(),str(source),'exec'),module.__dict__)
        test=ROOT/'workspace/task_heavy_task_1500/test_sfloadmacro.py'
        namespace={'__name__':'reviewed_static_fixture'}
        with patch.dict(sys.modules,{'sfloadmacro':module}):
            exec(compile(test.read_text(),str(test),'exec'),namespace)
        result=unittest.TestResult()
        unittest.defaultTestLoader.loadTestsFromTestCase(namespace['TestSFLoadMacro']).run(result)
        self.assertEqual(result.testsRun,1)
        self.assertTrue(result.wasSuccessful())

if __name__=='__main__':unittest.main(argv=['snapshot-hygiene'],exit=True)
