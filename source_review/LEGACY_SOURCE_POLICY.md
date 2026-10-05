# Retired/compatibility source policy for the proposed snapshot

Category 3 in `CLASSIFICATION.json` is source retained for review and archaeology, not production service code. Inclusion in Git does not authorize execution.

- `settlement_webhook.py`, `settlement_worker.py`, `webhook_listener.py`, `paypal_payouts.py`: top-level fail-closed retirement guards must remain.
- `dashboard.py`: financial/webhook shortcuts retired; do not deploy its unauthenticated work/UI routes as production authority.
- `acceptance_policy.py`, `pipeline.py`: explicit TEST/SANDBOX compatibility only; not canonical technical/contractual acceptance or finance.
- `settlement_engine.py`, `revenue_engine.py`: legacy accounting/state architecture; never use in production canonical authority flow.
- `git_adapter.py`, `patcher.py`, `src/*.py`, dispatchers/daemons, proposal/report generators and `test_harness.py`: legacy acquisition/workflow compatibility; do not start them to make the candidate appear operational. They may contact external systems or create local work/state.
- `create_sandbox_auth.py`, `paypal_sandbox_callback.py`, `test_paypal_auth.py`, `test_live_paypal_auth.py`, `test_paypal_capture_order.py`, `test_paypal_create_order.py`, `test_paypal_genuine_capture.py`, `test_paypal_live_reconciliation_observer.py`: historical/manual provider source only; some are executable or execute code at import. Do not run or discover them; the completed Sandbox authorization/capture must never be replayed.
- `test_chaos.py`: legacy webhook/network exerciser, excluded from approved regression execution.
- `purge_task.py`: destructive legacy database utility; do not run.

Some manual scripts have documentation retirement rather than an in-code guard; their source was preserved instead of overwritten merely to improve status. A source review should decide later whether to introduce explicit gates or move them into a manually invoked archive. They must remain out of deployed runtime entry points. Do not use unrestricted pytest/unittest discovery.

Approved regression command: `.\venv\Scripts\python.exe verify_production_candidate.py --receiver`. It uses a temporary database copy, blocks network in the parent and explicitly selects canonical scripts. The principal tests use local child processes with isolated configuration/database and no provider operations. `test_receiver_process_smoke.py` is a separate localhost-only manual process test; it was not executed in this snapshot review. Diagnostic/migration verification tools may read/write real state if launched directly, so source inclusion is not permission to execute them.


## Resolved source holds

`test_paypal_canonical_integration.py` is now an approved automated synthetic TEST-only integration: generated provider identifiers, mocked read evidence, isolated database, no provider client import. It is included explicitly in `verify_production_candidate.py`.

`test_paypal_capture_order.py` is permanently retired with a raise before any credential lookup/provider call; no historical order binding remains. `test_paypal_genuine_capture.py` remains manual-only (`__test__ = False`), with mandatory explicit settlement/expected amount/currency and opt-in confirmation before DB/provider access; it also requires canonical eligibility and keeps the existing durable capture/independent read-back/revenue checks. These tools were not invoked. Completed historical captures must never be replayed.

`data/mock_feed.json` and the two reviewed `workspace/task_heavy_task_1500` source files are static reproducibility exceptions, not permission to include surrounding runtime state. Other workspace files remain excluded.
