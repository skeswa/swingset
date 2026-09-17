-- Closed acquisition intervals are diagnostics, never completion or request authority.
CREATE TABLE event_timing (
    source TEXT NOT NULL, source_ref TEXT NOT NULL,
    episode_id TEXT NOT NULL, token_json TEXT NOT NULL, policy_json TEXT NOT NULL,
    observation_origin TEXT NOT NULL, saved_at TEXT NOT NULL,
    run_id TEXT NOT NULL, phase_open INTEGER NOT NULL,
    eligible_seconds REAL NOT NULL DEFAULT 0, blocked_seconds REAL NOT NULL DEFAULT 0,
    unknown_seconds REAL NOT NULL DEFAULT 0, inactive_seconds REAL NOT NULL DEFAULT 0,
    since_service_seconds REAL NOT NULL DEFAULT 0, since_progress_seconds REAL NOT NULL DEFAULT 0,
    complete_coverage INTEGER NOT NULL DEFAULT 1,
    service_bound_valid INTEGER NOT NULL DEFAULT 1, progress_bound_valid INTEGER NOT NULL DEFAULT 1,
    reason_seconds_json TEXT NOT NULL DEFAULT '{}',
    state TEXT NOT NULL, reason TEXT NOT NULL, valid_until TEXT NOT NULL,
    service_frontier INTEGER NOT NULL, progress_frontier INTEGER NOT NULL, operation_origin INTEGER NOT NULL,
    last_progress_at TEXT, last_service_action TEXT, last_service_at TEXT,
    unresolved_success INTEGER NOT NULL DEFAULT 0,
    control_revision INTEGER NOT NULL, marker_mtime_ns INTEGER NOT NULL,
    PRIMARY KEY(source,source_ref)
);
CREATE TABLE event_timing_history (
    episode_id TEXT NOT NULL, run_id TEXT NOT NULL, source TEXT NOT NULL, source_ref TEXT NOT NULL,
    summary_json TEXT NOT NULL,
    PRIMARY KEY(episode_id,run_id)
);
CREATE TABLE event_timing_cursor (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1), last_rowid INTEGER NOT NULL DEFAULT 0,
    high_water INTEGER NOT NULL DEFAULT 0
);
INSERT INTO event_timing_cursor(singleton) VALUES(1);
