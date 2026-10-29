"""Database schema definitions and table initializations."""

SQL_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA busy_timeout=5000;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS agents (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    status TEXT NOT NULL,
    reserved_by_worker TEXT,
    reserved_at REAL,
    current_call_id TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    last_state_change REAL NOT NULL,
    skills TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS idx_agents_status ON agents(status);
CREATE INDEX IF NOT EXISTS idx_agents_worker ON agents(reserved_by_worker);

CREATE TABLE IF NOT EXISTS borrowers (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    phone_number TEXT NOT NULL,
    campaign_id TEXT NOT NULL,
    status TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 100,
    retry_count INTEGER NOT NULL DEFAULT 0,
    max_retries INTEGER NOT NULL DEFAULT 3,
    cooldown_seconds REAL NOT NULL DEFAULT 60.0,
    last_attempt_at REAL,
    version INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_borrowers_status ON borrowers(campaign_id, status, priority DESC);

CREATE TABLE IF NOT EXISTS calls (
    id TEXT PRIMARY KEY,
    borrower_id TEXT NOT NULL,
    campaign_id TEXT NOT NULL,
    status TEXT NOT NULL,
    agent_id TEXT,
    worker_id TEXT,
    provider_id TEXT,
    provider_call_id TEXT,
    phone_number TEXT NOT NULL,
    created_at REAL NOT NULL,
    initiated_at REAL,
    ringing_at REAL,
    answered_at REAL,
    connected_at REAL,
    completed_at REAL,
    lease_expires_at REAL,
    abandoned INTEGER NOT NULL DEFAULT 0,
    failure_reason TEXT,
    version INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_calls_status ON calls(campaign_id, status);
CREATE INDEX IF NOT EXISTS idx_calls_lease ON calls(status, lease_expires_at);

CREATE TABLE IF NOT EXISTS call_events (
    id TEXT PRIMARY KEY,
    call_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    sequence_num INTEGER NOT NULL,
    idempotency_key TEXT UNIQUE NOT NULL,
    payload TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_call ON call_events(call_id, sequence_num);

CREATE TABLE IF NOT EXISTS campaigns (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    mode TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    target_abandonment_rate REAL NOT NULL,
    max_dial_multiplier REAL NOT NULL,
    default_talk_time_sec REAL NOT NULL,
    default_ring_time_sec REAL NOT NULL,
    default_wrap_up_sec REAL NOT NULL,
    min_agent_floor INTEGER NOT NULL,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS pacing_metrics (
    id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL,
    timestamp REAL NOT NULL,
    mode TEXT NOT NULL,
    available_agents INTEGER NOT NULL,
    connected_calls INTEGER NOT NULL,
    ringing_calls INTEGER NOT NULL,
    estimated_answer_rate REAL NOT NULL,
    pacing_proposed_dials INTEGER NOT NULL,
    safety_action TEXT NOT NULL,
    approved_dials INTEGER NOT NULL,
    reason TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pacing_metrics_ts ON pacing_metrics(campaign_id, timestamp);
"""
