# Tracked modifications inspected individually — 6 October 2026

No tracked file was overwritten, reverted, deleted or staged during snapshot preparation.

| Tracked modified file | Inspected change | Proposed treatment |
| --- | --- | --- |
| `dashboard.py` | Removed legacy settlement-webhook import, removed false-paid ledger mutation and merge-to-PayPal handling; retired routes return 410 and the financial shortcut raises. Credential-shaped fallback lookups are removed. Nonfinancial dashboard/workflow remains legacy. | Include current safety changes as explicitly retired/limited compatibility source; do not revert to HEAD, which restores unsafe financial shortcuts. Exclude dashboard from production service startup. |
| `settlement_webhook.py` | Top-level retirement guard before imports; previous live payout/webhook implementation replaced earlier by legacy settlement-engine routing. Old credential-shaped fallbacks removed. Remaining code is unreachable under its guard. | Include guarded retirement source. Preserve preceding local implementation changes; no rollback or execution. |
| `settlement_worker.py` | Top-level retirement guard; earlier working-tree edits add outbound payout helper/branching, cast capture amounts, change capture branch nesting and remove duplicate exception handlers. Those edits were already present before this review and remain unreachable under the guard. | Include only as guarded archaeology, never enable. The older functional changes merit later historical review, not automatic reversion. |
| `test_paypal_auth.py` | Adds pytest, replaces missing-credential/HTTP/connection boolean results with pytest failure/assertion; still performs real OAuth and includes provider response text in failure output. | Preserve and include as retired manual external-test source, not canonical regression. Never invoke/discover it during this review or deployment; later review may safely replace its provider-body output and add an execution gate. No changes made to it now. |
| `__pycache__/settlement_webhook.cpython-313.pyc` | Binary size changed from 7,958 to 2,314 bytes, consistent with regenerated cache for altered module. Binary contents were not treated as reviewable source. | Exclude from commit; retain locally. Human may remove from index using explicit removal manifest. No reset/discard needed. |
| `data/ledger.json` | JSON list changed from 76 to 79 entries; bytes from 31,423 to 33,123. Compared structurally without dumping customer/payment records. | Runtime/historical evidence: exclude changed contents, retain locally and outside Git. Do not revert, manufacture revenue or overwrite. Human index-only exclusion recommended. |
| `data/matched_jobs.json` | JSON list changed from 30 to 42 entries; bytes from 8,575 to 14,915. | Generated opportunity state: exclude, retain locally; human index-only exclusion recommended. |
| `data/triage.log` | Size grew from 251,087 to 255,206 bytes. Local execution log; no content dumped. | Exclude, retain locally; human index-only exclusion recommended. |

Additional inherited tracked anomalies: caches, generated proposals/patches/certificates/feeds/ledgers, workspaces, manual workflow text inputs and machine-local endpoint configuration are already in HEAD. `.gitignore` cannot untrack them. Every exact path is listed in `TRACKED_RUNTIME_REMOVAL_MANIFEST.txt`, with category/reason in `EXCLUSION_MANIFEST.json`. Human index removal changes the next tree while retaining all local files; it does not erase history. Existing credential exposure in history remains a separate rotation/history-cleanup concern.


## Source-hygiene resolution addendum

The reviewed safety edits to dashboard, settlement webhook and settlement worker were preserved byte-for-byte during hold resolution. `test_paypal_auth.py` remains unchanged, manual/noncanonical, and outside the approved regression list; its real OAuth capability/provider-response output remains a deployment exclusion, not an authorization to run it.

`test_paypal_capture_order.py` is now additionally modified: the historical order literal is removed, automatic discovery is disabled and its direct capture function permanently raises before credential/provider access. Include this guarded retirement change; do not revert it to HEAD. No runtime-data anomaly was reverted or overwritten. The three retained fixture/source exceptions are withdrawn from the tracked-removal manifest; generated neighbors remain excluded.
