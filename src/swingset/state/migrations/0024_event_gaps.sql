-- Gap observations do not create successful stage operations or completion authority.
CREATE TABLE event_gap_revisions (
    source TEXT PRIMARY KEY, revision INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE event_gap_observations (
    source TEXT NOT NULL, source_ref TEXT NOT NULL, request_id TEXT NOT NULL,
    enumeration_id TEXT NOT NULL REFERENCES source_event_enumerations(enumeration_id),
    observed_at TEXT NOT NULL, valid_until TEXT NOT NULL,
    availability INTEGER CHECK(availability IN (0,1)),
    token_json TEXT NOT NULL, source_revision INTEGER NOT NULL,
    evidence_json TEXT NOT NULL, evidence_digest TEXT NOT NULL,
    PRIMARY KEY(source,source_ref,request_id)
);
CREATE INDEX event_gap_observations_enumeration ON event_gap_observations(enumeration_id);
-- Absence proofs search all same-source snapshots, including undeclared aliases.
-- Keep their invalidation separate from missing-to-success progress baselines.
CREATE TRIGGER gap_snapshot_insert AFTER INSERT ON snapshots BEGIN
    INSERT INTO event_gap_revisions SELECT source,1 FROM watches WHERE watch_id=NEW.watch_id
    ON CONFLICT(source) DO UPDATE SET revision=revision+1;
END;
CREATE TRIGGER gap_snapshot_change
AFTER UPDATE OF snapshot_id,watch_id,method,url,form,body_sha256,fetched_at,classification,via,http_status,captured_at ON snapshots BEGIN
    INSERT INTO event_gap_revisions SELECT DISTINCT source,1 FROM watches WHERE watch_id IN (OLD.watch_id,NEW.watch_id)
    ON CONFLICT(source) DO UPDATE SET revision=revision+1;
END;
CREATE TRIGGER gap_snapshot_delete AFTER DELETE ON snapshots BEGIN
    INSERT INTO event_gap_revisions SELECT source,1 FROM watches WHERE watch_id=OLD.watch_id
    ON CONFLICT(source) DO UPDATE SET revision=revision+1;
END;
CREATE TRIGGER gap_watch_source AFTER UPDATE OF source ON watches
WHEN OLD.source IS NOT NEW.source BEGIN
    INSERT INTO event_gap_revisions VALUES(OLD.source,1) ON CONFLICT(source) DO UPDATE SET revision=revision+1;
    INSERT INTO event_gap_revisions VALUES(NEW.source,1) ON CONFLICT(source) DO UPDATE SET revision=revision+1;
    UPDATE event_pressure_state SET epoch=epoch+1;
END;
CREATE TRIGGER gap_watch_delete BEFORE DELETE ON watches BEGIN
    INSERT INTO event_gap_revisions VALUES(OLD.source,1) ON CONFLICT(source) DO UPDATE SET revision=revision+1;
END;
-- Rare support rewrites must also invalidate positive observations from aliases.
-- Ordinary inserts and parse-status changes still permit progress qualification.
CREATE TRIGGER gap_snapshot_support_rewrite
AFTER UPDATE OF classification,via,fetched_at,http_status,captured_at ON snapshots
WHEN OLD.classification IS NOT NEW.classification OR OLD.via IS NOT NEW.via
 OR OLD.fetched_at IS NOT NEW.fetched_at OR OLD.http_status IS NOT NEW.http_status
 OR OLD.captured_at IS NOT NEW.captured_at BEGIN
    UPDATE event_pressure_state SET epoch=epoch+1;
END;
