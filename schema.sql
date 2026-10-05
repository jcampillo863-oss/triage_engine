-- Database Substrate Schema for Dell Precision

CREATE TABLE IF NOT EXISTS patch_telemetry (
    telemetry_id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    domain_category TEXT NOT NULL,
    lines_added INTEGER,
    lines_deleted INTEGER,
    -- Pre-task Predictions (Recorded BEFORE execution)
    predicted_difficulty REAL,
    predicted_acceptance_probability REAL,
    predicted_execution_cost_usd REAL,
    predicted_completion_time_sec INTEGER,
    -- Post-execution Outcomes (Recorded AFTER external review)
    actual_execution_cost_usd REAL,
    actual_completion_time_sec INTEGER,
    actual_outcome TEXT, -- 'ACCEPTED', 'REJECTED', 'ABORTED'
    maintainer_feedback TEXT,
    revision_cycles INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS raw_evidence (
    evidence_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    git_commit_hash TEXT NOT NULL,
    pytest_exit_code INTEGER,
    syntax_valid BOOLEAN,
    compilation_valid BOOLEAN,
    diff_policy_passed BOOLEAN,
    raw_payload TEXT, -- JSON string of complete test output & environment facts
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS validation_results (
    result_id INTEGER PRIMARY KEY AUTOINCREMENT,
    validator_id TEXT NOT NULL,
    evidence_id TEXT NOT NULL,
    status TEXT NOT NULL, -- 'PASSED', 'FAILED', 'ERROR', 'SKIPPED', 'INCONCLUSIVE'
    checks_json TEXT,     -- JSON string of individual check statuses
    confidence REAL,      -- NULL until calibrated against telemetry
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (evidence_id) REFERENCES raw_evidence(evidence_id)
);

CREATE TABLE IF NOT EXISTS revenue_ledger (
    transaction_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    provider_id TEXT NOT NULL, -- 'paypal', 'stripe', 'bank'
    authorization_id TEXT NOT NULL,
    state TEXT NOT NULL,       -- 'PAYMENT_AUTHORIZED', 'CAPTURE_REQUESTED', 'OUTCOME_UNKNOWN', 'RECONCILING', 'PROVIDER_CONFIRMED', 'REVENUE_RECORDED', 'FAILED'
    amount_cents INTEGER NOT NULL,
    currency TEXT DEFAULT 'USD',
    provider_response TEXT,   -- JSON response body from provider
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);