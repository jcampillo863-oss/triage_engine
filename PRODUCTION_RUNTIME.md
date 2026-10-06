# Canonical runtime procedure

Run from the checkout root. The receiver reads process environment variables; it does not auto-load `.env`. Use a protected service configuration or local secret manager; do not put secret values on a command line. `.env.example` contains names and blank placeholders only. The receiver starts disabled unless explicitly configured. Live capture defaults off independently.

## Fresh checkout: Canonical Bootstrap V1

Create the virtual environment and install only declared canonical runtime dependencies:

```powershell
python -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements-canonical-runtime.txt
.\venv\Scripts\python.exe canonical_bootstrap.py
.\venv\Scripts\python.exe -B test_canonical_bootstrap.py
.\venv\Scripts\python.exe verify_production_candidate.py --receiver
```

Bootstrap defaults to this checkout's `data/settlement.db`, independently of the current working directory. An explicit `--db <path>` selects another database; pass the same path to the verifier. Missing parent directories are created. No historical database, `.env`, provider credentials or principal secrets are required. Bootstrap performs no network or financial operations and does not enable capture or the receiver.

The substrate owner remains `db.init_db()`. Bootstrap uses a fixed ordered registry of migrations 001–010; it never executes the divergent legacy `schema.sql`. Migration 007 remains an explicitly verified, historically unregistered schema helper. Expected metadata versions are 1–6, 8, 9 and 10; bootstrap does not fabricate a version-7 application timestamp.

A missing database is built privately on the target filesystem, checked against a fresh committed-schema reference, checked for integrity/FKs and empty evidence/economic tables, then checkpointed and published without overwriting any target. Publication requires filesystem hard-link support (supported by the intended local NTFS deployment); unsupported filesystems fail closed. Failed construction removes only bootstrap-owned temporary files and leaves no published partial database. This is atomic publication, not a transaction spanning the independently committing migrations. Concurrent publication cannot replace another process's target.

A current database is validated read-only and left unchanged, including historical rows, metadata and capture claims. Partial, older, future or conflicting schemas are rejected for separate human-reviewed upgrade; bootstrap never upgrades a historical database automatically. Exact committed schema definitions are required, including indexes and triggers; additional schema objects also require review. Use bootstrap in a dedicated process because the existing components use process-local database bindings.

Verification copies the selected existing database into an independent temporary directory, redirects approved fixtures requesting checkout `data/` into that same isolated run, blocks application network access and runs the approved canonical script list. It never bootstraps its input. Integrity or foreign-key failures cause a failing exit status. Many individual regression scripts create their own isolated fixtures; fresh construction itself is covered separately by `test_canonical_bootstrap.py`. `verify_canonical_suite.py` is an empty-state diagnostic, not a readiness gate for databases containing legitimate economic history. Do not use unrestricted test discovery or genuine/manual provider scripts.

The local receiver-process smoke procedure below is a separate deployment check with existing local virtual-environment assumptions, not a bootstrap prerequisite. Dependency locking and general portability/legacy cleanup are outside Bootstrap V1.

Before startup, stop legacy services, back up `data/settlement.db` using SQLite's backup API (including active WAL state), restrict file permissions to authorized actors, preserve historical evidence, and run:

```powershell
.\venv\Scripts\python.exe verify_production_candidate.py --receiver
.\venv\Scripts\python.exe test_receiver_process_smoke.py
```

Both checks copy the database; they do not contact GitHub/PayPal. The process smoke test starts and stops a temporary localhost receiver. Never substitute an unrestricted test discovery command: historical scripts contain provider operations.

After backup, with financial processes stopped, install the additive claim schema:

```powershell
.\venv\Scripts\python.exe migration_007_capture_attempt_guard.py
```

This does not call a provider. It conservatively marks already-prepared captures as claimed; they require observation and must not be dispatched. The same schema helper is used defensively on new capture preparation. No original database migration was run during the sprint.

Supply `GITHUB_REPOSITORY`, `GITHUB_WEBHOOK_SECRET`, `ACCEPTANCE_ENVIRONMENT=SANDBOX` for initial genuine GitHub testing, and `GITHUB_ACCEPTANCE_ENABLED=true` through the service's protected environment. A GitHub repository can support Sandbox acceptance: only previously recorded Sandbox deliveries bind. Start the receiver:

```powershell
.\venv\Scripts\python.exe github_webhook_receiver.py
Invoke-RestMethod http://127.0.0.1:8091/health
```

Healthy means HTTP 200 and `ready=true`; HTTP 503 means stopped, disabled, missing configuration/schema/database or unavailable storage. The only event endpoint is POST `/webhook/github`. Do not publish port 8091 directly. Configure HTTPS ingress, body/header/time limits and monitoring at an explicit external-infrastructure checkpoint. Forward raw bodies without rewriting them. GitHub should send pull-request events with `X-Hub-Signature-256`, `X-GitHub-Delivery` and `X-GitHub-Event`. The customer contract must authorize merge as contractual acceptance. An HTTP 409 indicates binding/evidence conflict requiring investigation, not permission to alter task identity. Exact GitHub redelivery is idempotent; changed evidence under the same ID is rejected. Secret rotation may change the signature without changing raw body evidence.

Receiver audit records contain timestamps and HTTP outcomes, never payloads or signatures. Configure persistent stdout/stderr collection with retention and access controls before deployment. Accepted raw bodies remain in canonical evidence storage; treat them as potentially confidential repository data. GitHub requests and finance are separate processes and roles.

Safe stop:

```powershell
New-Item -ItemType File -Path .\data\STOP -Force
```

The sentinel blocks new webhook admission and canonical capture preparation/dispatch, including Sandbox. It cannot cancel a provider request already in flight. Stop the receiver with Ctrl+C or through its service supervisor; leave the sentinel in place through investigation. Explicitly set `CANONICAL_LIVE_CAPTURE_ENABLED=false` in the financial service configuration. Do not change `PAYPAL_MODE` or use legacy workers. Read-only reconciliation remains available while stopped. Requests admitted just before the sentinel appeared may finish recording acceptance; they have no financial authority.

Recovery: verify database integrity and foreign keys, inspect canonical journal and durable attempt claims, and make read-only provider observations. `OUTCOME_UNKNOWN` resumes through `RECONCILING`; `NOT_CAPTURED` remains reconciling. A `CAPTURE_REQUESTED` settlement with a claim may represent a crashed/in-flight operation: first stop the owning process, then an audited operator recovery transitions it to `OUTCOME_UNKNOWN` with the claim reference and commits before read-only reconciliation. Never delete the claim or call capture again. The same rule applies to pre-upgrade claims. Manually escalate unresolved evidence. A claim may exist even if the process died before sending; that is intentionally conservative. Restore only from a reviewed consistent backup, and reconcile provider state before any new dispatch to avoid losing a durable claim.

Remove `data/STOP` only at the next explicitly approved activation checkpoint, after confirming no in-flight financial process remains. Do not automate removal on startup. Production approval also requires reviewed financial execution/client/validation gates; enabling the environment flag alone is insufficient.


## Canonical eligibility prerequisite — 5 October 2026

Migration 008 has been applied locally after verified backup. For another database, use `migration_008_settlement_eligibility.py`; it performs its own backup before the additive migration. Never manually backfill producers, commits or approvals for incomplete historical deliveries. Run `verify_production_candidate.py --receiver` for isolated network-blocked regression coverage. `verify_eligibility_migration.py` compares the current database with its most recent migration-008 backup immediately after migration; it is not intended to run after legitimate new production data has been written.

New settlement creation now requires `eligibility_decision_id`. The approval is issued separately by `approve_settlement_eligibility` from the exact recorded work/evidence/validation/technical/contractual chain. Producer and validator must have distinct trusted stable principal IDs, and evidence must match the dispatched commit exactly. The producer/validator identity provisioning mechanism remains a deployment responsibility. The settlement engine consumes this approval and verified payment authorization; it does not evaluate technical or GitHub semantics. Old settled records remain historical and receive no fabricated approval.


## Authenticated Principal Boundary V1 — 5 October 2026

Production work and validation operations now require separate protected service secrets: `MARKETPLACE_PRODUCER_SECRET` and `MARKETPLACE_VALIDATOR_SECRET`. Configure independent URL-safe base64 secrets generated from at least 32 random bytes; do not pass values on command lines, commit them or share the service's secret environment with untrusted workers. No actual secrets were provisioned during implementation. No database migration is required.

Use `create_delivery(..., environment="PRODUCTION", producer_credential=...)` without a `producer_id` argument. Submit explicit observations through `record_work_evidence`, which derives task/commit from the immutable delivery. Independently call `validate_work(..., validator_credential=..., work_delivery_id=..., evidence_id=...)` without a `validator_id` or verdict. Its validation/technical result can then be consumed by the existing eligibility authority after independently authenticated contractual acceptance. This sequence performs no payment. Preserve protected transport/argument handling in the trusted service; no public authority endpoint was added.

The implicit local validator is blocked. `LocalValidator`, legacy evidence persistence and `acceptance_policy` persistence are compatibility-only with explicit TEST/SANDBOX selection; production-linked compatibility writes fail closed. Keep `pipeline.py` and legacy acceptance/payment listeners out of production. The trusted service alone should control canonical DB writes; arbitrary in-process code or direct SQL access is outside this credential boundary.

Collect the `marketplace.authority` logger at INFO level to a restricted persistent audit sink. It emits canonical principal ID, capability, operation, outcome and timestamp only. Keep authentication argument/local-variable capture disabled in request/error instrumentation. Repeated validation after restart is idempotent. Next-call authentication uses current protected environment configuration; missing or malformed/equal secrets deny operations. Provisioning these secrets does not enable financial capture or change GitHub authentication.

## Production payment boundary (offline implementation; activation separately approved)

Fresh bootstrap includes forward migrations 009 and 010. SQLite JSON functions are required and checked before schema creation. Existing databases are never upgraded
by bootstrap. Applying 009 requires a separately approved WAL-aware backup, stopped
financial execution, isolated validation and an explicit reviewed upgrade. Historical
Sandbox rows acquire no Production provenance.

Use paypal_client.PayPalClient("PRODUCTION") only with protected
PAYPAL_PRODUCTION_CLIENT_ID / PAYPAL_PRODUCTION_CLIENT_SECRET. SANDBOX uses only
the dedicated SANDBOX names. Endpoints are fixed; generic credentials, PAYPAL_MODE,
caller URLs, redirects, proxy-environment routing and mutation retries are unsupported.
Production credentials are not provisioned by this source change.

The trusted financial service alone runs production_payment_flow: register and
commit an exact obligation backed by approved eligibility; explicitly approve order
creation; retrieve the validated customer URL with client.get_approval_url(order_id); let the buyer approve the bound AUTHORIZE order through PayPal; separately
approve merchant authorization; retrieve and verify the exact authorization and order;
approve canonical payment authorization from the opaque provider receipt; commit;
then create and commit the matching canonical settlement. A return token or
authenticated=True dictionary cannot establish Production payment authority.
Receipts protect the API boundary, not against arbitrary code execution in the trusted
service process. Order creation/authorization claims are committed before dispatch.
If a process dies or a provider result is unclear, observe the existing resource;
never blindly reissue the operation. Restarts re-retrieve authoritative evidence.

Capture is a separate approval. Keep CANONICAL_LIVE_CAPTURE_ENABLED=false until
explicit authorization. The same dedicated one-shot process must also receive protected
CANONICAL_CAPTURE_APPROVAL JSON containing exactly settlement_id, obligation_id,
eligibility_decision_id, payment_authorization_id, environment, amount_cents,
currency, and timezone-aware expires_at (at most 30 minutes ahead).
All terms must match persisted state. Do not put credential values on command lines.
Use capture_once for that one settlement; no polling, discovery, Payouts or legacy workers.
Missing/expired/mismatching approval, STOP or missing claim schema fails closed.
STOP applies to capture preparation/dispatch, not to an already in-flight request.

Production capture specifies the exact amount and final_capture=true. A durable unique
claim precedes dispatch. The Production client requires a single-use permit derived from that committed claim; the exact transaction gate is checked again after OAuth and immediately before capture dispatch. Uncertain provider responses enter OUTCOME_UNKNOWN and cannot
be retried. After a crash with a claimed CAPTURE_REQUESTED state, follow the existing
supervised recovery procedure above. Reconciliation uses only GET resource operations
(after OAuth authentication), validates exact resource relationships and terms, and
never captures/refunds/voids/reauthorizes. NOT_CAPTURED remains inconclusive.
Revenue is a separate explicitly authorized operation.

Offline checks: python -B test_production_payment_boundary.py,
python -B test_canonical_bootstrap.py, and
python verify_production_candidate.py --db <isolated-or-read-only-source-db> --receiver.
Never run genuine/manual provider tests or enable credentials as part of verification.

### Adversarial remediation: capture provenance and readiness

Production capture persistence accepts only opaque CaptureEvidence returned by the
fixed-environment provider client after a capture response or capture-resource GET.
Caller dictionaries and normalization results cannot be authoritative. Persistence
rechecks the bound authorization/order/payee/terms, requires the existing durable claim,
records the provenance source and hash, and freezes the resulting proof. Confirmation
and revenue recheck that persisted proof. Sandbox compatibility data cannot promote.

Migration 009 also freezes existing capture claims against deletion, identity/timestamp
updates and replacement/conflict bypasses. Helper 007 is unchanged. A lost process-local
permit after a committed claim requires observation, never a new capture dispatch.

OAuth/network/non-success read responses mean observation unavailable: reconciliation
stays durably RECONCILING and can resume with a later GET. Contradictory/malformed
authoritative resources still fail closed under the existing manual-review policy.
No capture is retried and terminal-state semantics remain unchanged.

The verifier defaults to actual supplied-database readiness. It validates an unchanged
SQLite snapshot against the exact current schema, including every required trigger,
index, constraint and migration identity, before running isolated regressions.
Use --schema-only for that read-only structural/integrity/FK gate.
Use --migration-compatibility only to test forward migration of a private copy of an
older database; its output explicitly does not establish readiness of the original.
Older-schema compatibility checks apply forward migrations only to a private copy,
then run the same complete bounded semantic schema check used by readiness mode.
They do not certify readiness of the unmodified supplied database. Missing financial
or evidence enforcement fails even when migration metadata is present.

Trusted-process assumption: arbitrary code execution, direct SQL forgery, private
receipt-factory/transport access or protected-environment modification inside the
financial process remain outside this API boundary. Untrusted work, tests and plugins
must not execute in the credential-bearing financial process. Offline tests run with
fake credentials in isolated processes/databases and cannot establish live authority.


### Forward evidence contract and historical schema readiness

Fresh bootstrap applies migration 010 after 009. It does not upgrade an existing
older database. An upgrade requires a separately approved, backed-up operation;
no live upgrade is authorized by the development/testing procedure.

Migration 010 leaves every historical row untouched. New inserts and updates
require a nonblank text commit, integer observation flags in {0,1}, and a nullable
integer pytest exit code. Validation checks must serialize a JSON object whose
named values are canonical check-status strings. An empty object remains valid
compatibility serialization; it does not itself establish technical acceptance.
Duplicate evidence/result identities cannot be replaced, even with SQLite conflict
clauses and recursive triggers disabled. Existing eligibility freezes remain intact.

Readiness requires the complete current schema, including exact enforcement-token
bodies, FKs, CHECK constraints and indexes. SQL whitespace/comments and keyword
case may vary. Only the audited historical raw_evidence and validation_results
base forms are additionally permitted, and only with all migration-010 guards.
The documented legacy patch_telemetry, settlements and settlement_journal tables
may remain with their audited definitions. They are not canonical financial
execution authorities. Unknown additional objects or missing guards fail closed.
Migration metadata alone never establishes readiness.
