"""Bounded schema readiness: token-equivalent SQL plus enumerated historical forms.

No arbitrary schema relaxation: all canonical objects remain required, including
010 guards. Only the audited three legacy tables and two base-table forms differ.
Stored strings are preserved; comments/whitespace and keyword case are not authority.
"""
import re

LEGACY_TABLES = {'patch_telemetry': "CREATE TABLE patch_telemetry (\n            telemetry_id INTEGER PRIMARY KEY AUTOINCREMENT,\n            task_id TEXT NOT NULL,\n            domain_category TEXT NOT NULL,\n            lines_added INTEGER,\n            lines_deleted INTEGER,\n            -- Pre-task Predictions (Recorded BEFORE execution)\n            predicted_difficulty REAL,\n            predicted_acceptance_probability REAL,\n            predicted_execution_cost_usd REAL,\n            predicted_completion_time_sec INTEGER,\n            -- Post-execution Outcomes (Recorded AFTER external review)\n            actual_execution_cost_usd REAL,\n            actual_completion_time_sec INTEGER,\n            actual_outcome TEXT, -- 'ACCEPTED', 'REJECTED', 'ABORTED'\n            maintainer_feedback TEXT,\n            revision_cycles INTEGER DEFAULT 0,\n            created_at TEXT NOT NULL\n        )", 'settlements': "CREATE TABLE settlements (\n            settlement_id TEXT PRIMARY KEY,\n            task_id TEXT NOT NULL,\n            pr_url TEXT NOT NULL,\n            amount TEXT NOT NULL,\n            currency TEXT NOT NULL DEFAULT 'AUD',\n            recipient_email TEXT NOT NULL,\n            state TEXT NOT NULL DEFAULT 'AUTHORIZED',\n            provider_batch_id TEXT UNIQUE,\n            provider_item_id TEXT,\n            attempt_count INTEGER DEFAULT 0,\n            last_error TEXT,\n            created_at TEXT NOT NULL,\n            updated_at TEXT NOT NULL\n        )", 'settlement_journal': 'CREATE TABLE settlement_journal (\n            id INTEGER PRIMARY KEY AUTOINCREMENT,\n            settlement_id TEXT NOT NULL,\n            from_state TEXT,\n            to_state TEXT NOT NULL,\n            reason TEXT,\n            provider_payload TEXT,\n            timestamp TEXT NOT NULL,\n            FOREIGN KEY (settlement_id) REFERENCES settlements (settlement_id)\n        )'}

HISTORICAL_BASE_TABLES = {'raw_evidence': 'CREATE TABLE raw_evidence (\n            evidence_id TEXT PRIMARY KEY,\n            task_id TEXT NOT NULL,\n            git_commit_hash TEXT NOT NULL,\n            pytest_exit_code INTEGER,\n            syntax_valid BOOLEAN,\n            compilation_valid BOOLEAN,\n            diff_policy_passed BOOLEAN,\n            raw_payload TEXT, -- JSON string of complete environment/execution facts\n            created_at TEXT NOT NULL\n        )', 'validation_results': "CREATE TABLE validation_results (\n            result_id INTEGER PRIMARY KEY AUTOINCREMENT,\n            validator_id TEXT NOT NULL,\n            evidence_id TEXT NOT NULL,\n            status TEXT NOT NULL, -- 'PASSED', 'FAILED', 'ERROR', 'SKIPPED', 'INCONCLUSIVE'\n            checks_json TEXT,     -- JSON string of individual check statuses\n            confidence REAL,      -- NULL until empirically calibrated\n            created_at TEXT NOT NULL,\n            FOREIGN KEY (evidence_id) REFERENCES raw_evidence(evidence_id)\n        )"}

_TOKEN = re.compile(r"--[^\n]*|/\*.*?\*/|'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|\[(?:[^\]])*\]|`(?:``|[^`])*`|[A-Za-z_][A-Za-z_0-9]*|[0-9]+(?:\.[0-9]+)?|[^\s]", re.S)


def sql_tokens(sql):
    if sql is None:
        return None
    tokens = []
    for token in _TOKEN.findall(sql):
        if token.startswith('--') or token.startswith('/*'):
            continue
        tokens.append(token if token[0] in "'\"[`" else token.lower())
    return tuple(tokens)


def compatible_schema(actual, reference):
    actual = {name: (kind, table, sql_tokens(sql)) for kind,name,table,sql in actual}
    reference = {name: (kind, table, sql_tokens(sql)) for kind,name,table,sql in reference}
    # Guard bodies must also match the reference, not just migration metadata/names.
    for table in HISTORICAL_BASE_TABLES:
        for suffix in ('insert','update','no_replace','no_update_replace'):
            name = 'forward_evidence_' + table + '_' + suffix
            if name not in reference or actual.get(name) != reference[name]:
                return False
    for name, expected in reference.items():
        found = actual.get(name)
        if found == expected:
            continue
        permitted = HISTORICAL_BASE_TABLES.get(name)
        if not permitted or found != ('table',name,sql_tokens(permitted)):
            return False
    for name in actual.keys() - reference.keys():
        permitted = LEGACY_TABLES.get(name)
        if not permitted or actual[name] != ('table',name,sql_tokens(permitted)):
            return False
    return True
