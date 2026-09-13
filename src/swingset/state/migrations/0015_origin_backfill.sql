CREATE TABLE history_origin_intents (
    watch_id TEXT PRIMARY KEY REFERENCES watches(watch_id),
    source TEXT NOT NULL, source_ref TEXT NOT NULL, event_id TEXT NOT NULL,
    page_kind TEXT NOT NULL, url TEXT NOT NULL, parent_url TEXT,
    evidence_json TEXT NOT NULL, created_at TEXT NOT NULL,
    dispatch_run_id TEXT REFERENCES runs(run_id)
);
CREATE INDEX history_origin_intents_event ON history_origin_intents(source,event_id);
CREATE INDEX history_origin_intents_run ON history_origin_intents(dispatch_run_id);
CREATE TABLE history_origin_requests (
    action_id TEXT PRIMARY KEY REFERENCES execution_admissions(action_id),
    watch_id TEXT NOT NULL REFERENCES watches(watch_id),
    source TEXT NOT NULL, event_id TEXT NOT NULL,
    run_id TEXT NOT NULL REFERENCES runs(run_id), day TEXT NOT NULL,
    issued_at TEXT NOT NULL
);
CREATE INDEX history_origin_requests_cycle ON history_origin_requests(run_id,source,event_id);
CREATE INDEX history_origin_requests_day ON history_origin_requests(source,day,event_id);
CREATE INDEX history_origin_requests_watch ON history_origin_requests(watch_id);
CREATE TABLE history_origin_operator_refs (
    source_ref TEXT PRIMARY KEY,
    evidence_reference TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);
UPDATE meta SET value='15' WHERE key='schema_version';
PRAGMA user_version=15;
