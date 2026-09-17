-- Observed facts at sample times, never continuous eligibility intervals.
CREATE TABLE event_blocker_policies (digest TEXT PRIMARY KEY, policy_json TEXT NOT NULL);
CREATE TABLE event_blocker_refreshes (
    refresh_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(run_id), observed_at TEXT NOT NULL,
    summary_json TEXT NOT NULL
);
CREATE INDEX event_blocker_refreshes_run ON event_blocker_refreshes(run_id,refresh_id);
CREATE TABLE event_blocker_observations (
    observation_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL, source_ref TEXT NOT NULL,
    refresh_id INTEGER NOT NULL REFERENCES event_blocker_refreshes(refresh_id),
    observed_at TEXT NOT NULL, signature TEXT NOT NULL,
    policy_digest TEXT NOT NULL REFERENCES event_blocker_policies(digest), facts_json TEXT NOT NULL
);
CREATE INDEX event_blocker_observations_event ON event_blocker_observations(source,source_ref,observation_id);
CREATE INDEX event_blocker_observations_refresh ON event_blocker_observations(refresh_id);
CREATE INDEX event_blocker_observations_policy ON event_blocker_observations(policy_digest);
CREATE TABLE event_blocker_latest (
    source TEXT NOT NULL, source_ref TEXT NOT NULL,
    observation_id INTEGER NOT NULL REFERENCES event_blocker_observations(observation_id),
    PRIMARY KEY(source,source_ref)
);
CREATE INDEX event_blocker_latest_observation ON event_blocker_latest(observation_id);
CREATE TABLE event_blocker_cursor (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1), last_rowid INTEGER NOT NULL DEFAULT 0,
    high_water INTEGER NOT NULL DEFAULT 0, pass INTEGER NOT NULL DEFAULT 0
);
INSERT INTO event_blocker_cursor(singleton) VALUES (1);
CREATE INDEX watches_event_blockers ON watches(source,source_ref,watch_id);
CREATE TRIGGER event_blocker_observation_no_update BEFORE UPDATE ON event_blocker_observations
BEGIN SELECT RAISE(ABORT,'blocker observations are immutable'); END;
CREATE TRIGGER event_blocker_observation_no_delete BEFORE DELETE ON event_blocker_observations
BEGIN SELECT RAISE(ABORT,'blocker observations are immutable'); END;
CREATE TRIGGER event_blocker_refresh_no_update BEFORE UPDATE ON event_blocker_refreshes
BEGIN SELECT RAISE(ABORT,'blocker refresh summaries are immutable'); END;
CREATE TRIGGER event_blocker_refresh_no_delete BEFORE DELETE ON event_blocker_refreshes
BEGIN SELECT RAISE(ABORT,'blocker refresh summaries are immutable'); END;
CREATE TRIGGER event_blocker_policy_no_update BEFORE UPDATE ON event_blocker_policies
BEGIN SELECT RAISE(ABORT,'blocker policies are immutable'); END;
CREATE TRIGGER event_blocker_policy_no_delete BEFORE DELETE ON event_blocker_policies
BEGIN SELECT RAISE(ABORT,'blocker policies are immutable'); END;
