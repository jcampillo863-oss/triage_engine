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

The substrate owner remains `db.init_db()`. Bootstrap uses a fixed ordered registry of migrations 001–008; it never executes the divergent legacy `schema.sql`. Migration 007 remains an explicitly verified, historically unregistered schema helper. Expected metadata versions are 1–6 and 8; bootstrap does not fabricate a version-7 application timestamp.

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
