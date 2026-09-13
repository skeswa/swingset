CREATE TABLE scheduler_requests (
    action_id TEXT PRIMARY KEY REFERENCES execution_admissions(action_id),
    host TEXT NOT NULL, day TEXT NOT NULL, category TEXT NOT NULL,
    work_key TEXT NOT NULL, run_id TEXT REFERENCES runs(run_id),
    repair INTEGER NOT NULL DEFAULT 0, pressure_reserved INTEGER NOT NULL DEFAULT 0,
    issued_at TEXT NOT NULL,
    body_bytes INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX scheduler_requests_class ON scheduler_requests(day,host,category);
CREATE INDEX scheduler_requests_run ON scheduler_requests(run_id,pressure_reserved);
CREATE INDEX scheduler_requests_service ON scheduler_requests(host,category,issued_at);
CREATE TABLE scheduler_offline_service (
    stage TEXT NOT NULL, unit_kind TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0, last_served_at TEXT NOT NULL,
    sequence INTEGER NOT NULL, PRIMARY KEY(stage,unit_kind)
);
CREATE TABLE scheduler_watch_state (
    watch_id TEXT PRIMARY KEY REFERENCES watches(watch_id),
    metadata_attempts INTEGER NOT NULL DEFAULT 0, last_metadata_attempt_at TEXT
);
CREATE TABLE scheduler_parent_links (
    parent_watch_id TEXT NOT NULL REFERENCES watches(watch_id),
    child_watch_id TEXT NOT NULL REFERENCES watches(watch_id),
    first_snapshot_id TEXT REFERENCES snapshots(snapshot_id), first_seen_at TEXT,
    PRIMARY KEY(parent_watch_id,child_watch_id)
);
CREATE INDEX scheduler_parent_links_child ON scheduler_parent_links(child_watch_id);
CREATE INDEX scheduler_parent_links_snapshot ON scheduler_parent_links(first_snapshot_id);
INSERT INTO scheduler_parent_links(parent_watch_id,child_watch_id,first_snapshot_id)
SELECT parent_watch_id,watch_id,created_by_snapshot_id FROM watches WHERE parent_watch_id IS NOT NULL;
