# Canonical eligibility boundary — 5 October 2026

Implemented the requested boundary without changing the four authorities or settlement state graph.

Files changed:
- `work_delivery.py`: optional producer identity at creation; serialized, strict producer/commit replay comparisons. Incomplete deliveries remain recordable but cannot qualify for settlement.
- `settlement_eligibility.py` (new): `approve_settlement_eligibility(conn, ...)` issues an immutable, deterministic approval linking task, delivery, evidence, validation, technical decision, contractual decision and environment. Caller owns the transaction.
- `migration_008_settlement_eligibility.py` (new): additive producer/eligibility columns, eligibility table, foreign keys and immutable-chain/replacement guards. Applies atomically after verified SQLite backup.
- `canonical_settlement_engine.py`: new settlements require `eligibility_decision_id`; approved artifact must match task, environment and contractual decision. Existing verified authorization checks remain. Exact settlement replay is retained; conflicting replay is rejected. No GitHub, evidence, test or validator evaluation was added to this engine.
- `eligibility_test_support.py`, `test_settlement_eligibility.py` (new): explicit synthetic chains and isolated tests.
- `test_canonical_settlement_engine.py`, `test_canonical_invariants.py`, `test_canonical_revenue.py`: fixtures now supply test-only eligibility; previous assertions retained.
- `verify_production_candidate.py`: migrates only its isolated database copy before regression tests; blocks network; includes new eligibility tests and keeps temporary databases inside this project.
- `verify_eligibility_migration.py` (new): read-only comparison against the original pre-migration backup.
- This report, runtime instructions and prior readiness-report addendum.

Invariants enforced:
- Producer identity and dispatched commit cannot be changed, including missing historical values. Conflicting delivery replay or SQL replacement fails.
- Delivery and raw evidence must carry the same nonblank dispatched commit, with exact equality.
- Task and environment form one chain; validation references the exact evidence; accepted technical decision references that same evidence/result; validation is PASSED.
- Producer and validator identities are present, canonical nonblank strings and unequal.
- Accepted contractual decision points to authenticated evidence for this task, delivery, environment and repository/PR binding.
- Approval does not authorize payment or create settlement/revenue records. Its chain and snapshot fields are checked at insertion and then frozen, including referenced evidence. SQL UPDATE/DELETE/REPLACE cannot invalidate the approval or silently rebind a settlement.
- Settlement creation consumes approved matching eligibility and then the existing verified payment authorization checks. Missing or conflicting eligibility fails closed.

Migration status: migration 008 applied to the original database after a verified backup in `data/backups/pre-eligibility-008-20261004T160313827751Z.db` (UTC timestamp). All pre-existing rows in every original table were compared against the backup and preserved. No producer identities or eligibility decisions were backfilled; historical settlement eligibility remains NULL. The one genuine historical Sandbox REVENUE_RECORDED record, ledger and journal are unchanged. Historical records remain readable through `get_settlement`; they are not newly approved through `create_settlement`.

Validation: the final isolated regression run passed all 15 existing canonical scripts, 12 receiver tests, 5 capture-safety tests and 33 new eligibility tests. Coverage includes every requested negative case, approved creation/replay, conflicting settlement replay, immutable dependency/replacement protections, restart replay, authority separation and nonempty historical migration with a revenue ledger/journal fixture. Original and isolated database integrity checks passed with zero foreign-key errors. Changed Python files passed syntax checks. No PayPal, GitHub or public endpoint was called; no live credentials were used; capture configuration and Git history were untouched.

Usage: record a delivery with its trusted `producer_id` and `dispatched_commit_hash`; independently record evidence, validation and technical decision. Record contractual acceptance through its existing authority. Within a database transaction call `approve_settlement_eligibility` with the exact seven chain identifiers/environment, commit its approval, then pass its `decision_id` as `eligibility_decision_id` to `create_settlement` alongside independently verified payment authorization. Do not infer missing historical provenance or modify approved evidence; record a fresh legitimate delivery/chain when work changes.

Remaining blockers: no architectural conflict was found. Deployment still must authenticate and provision distinct stable producer/validator principal identities: this boundary checks stored identities and coherence, not account authentication or OS-role enforcement. Genuine GitHub delivery, rotated credentials, reviewed live PayPal client, approved ingress and the live activation procedure remain separate previously identified blockers. Production capture remains disabled by default. This change closes the requested eligibility gap; it is not approval for real-money activation.


## Authenticated principal addendum — 5 October 2026

Authenticated Principal Boundary V1 now derives production producer/validator identities from separate protected service credentials and rejects caller identity assertions. Canonical eligibility remains unchanged. Legacy local validator/policy writes are explicit TEST/SANDBOX compatibility only. See `PRINCIPAL_BOUNDARY_REPORT.md` for the exact API trust boundary and tests. Service/DB access isolation, credential provisioning, audit collection and the other live-activation blockers remain; this does not authorize live payments.
