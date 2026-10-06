# Repository source layout

Canonical Marketplace modules live at the repository root; older triage helpers remain in their own directory.

- [Work delivery](../work_delivery.py), [authenticated work](../authenticated_work.py) and [external acceptance](../external_acceptance.py) record the canonical work and acceptance chain.
- [Database substrate](../db.py), [canonical bootstrap](../canonical_bootstrap.py) and numbered [migration scripts](../migration_001_database_foundation.py) define and construct the database schema.
- Root `test_*.py` files include canonical regressions and separate manual or legacy checks. [Production-candidate verification](../verify_production_candidate.py) selects the approved canonical suite.
- [Runtime requirements](../requirements-canonical-runtime.txt) and the [runtime guide](../PRODUCTION_RUNTIME.md) describe canonical dependencies and operation.
- [src/](../src/) contains older feed, routing, proposal and dispatch helpers.
- [source_review/](../source_review/) retains source-snapshot classification and review artifacts.
- [docs/](./) contains repository documentation. [The layout document test](../test_repository_layout_doc.py) checks this page without importing application modules.
