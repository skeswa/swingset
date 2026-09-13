-- Check history is separate from the snapshot that supplied a claim.
CREATE TABLE registry_verifications (
    verification_id INTEGER PRIMARY KEY,
    watch_id TEXT NOT NULL REFERENCES watches(watch_id),
    checked_at TEXT NOT NULL,
    http_status INTEGER,
    body_sha256 TEXT,
    snapshot_id TEXT REFERENCES snapshots(snapshot_id),
    extract_version TEXT,
    parser_version TEXT,
    outcome TEXT CHECK (outcome IN ('found','not_found') OR outcome IS NULL),
    usable INTEGER NOT NULL DEFAULT 0 CHECK (usable IN (0,1)),
    reason TEXT NOT NULL,
    CHECK (usable=0 OR (outcome IS NOT NULL AND snapshot_id IS NOT NULL AND body_sha256 IS NOT NULL))
);
CREATE INDEX registry_verification_watch ON registry_verifications(watch_id, checked_at);
UPDATE meta SET value='2' WHERE key='schema_version';
