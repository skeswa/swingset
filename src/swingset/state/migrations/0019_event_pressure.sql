-- Scheduling hints only: no event-completion or stage authority.
CREATE TABLE event_pressure_state (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1), epoch INTEGER NOT NULL DEFAULT 0,
    sequence INTEGER NOT NULL DEFAULT 0
);
INSERT INTO event_pressure_state(singleton) VALUES (1);
CREATE TABLE event_pressure_subjects (
    source TEXT NOT NULL, source_ref TEXT NOT NULL, enrolled_at TEXT NOT NULL,
    start_basis TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0,
    checked_sequence INTEGER NOT NULL DEFAULT 0, scan_json TEXT, observation_json TEXT,
    PRIMARY KEY(source,source_ref)
);
CREATE INDEX event_pressure_subjects_refresh ON event_pressure_subjects(checked_sequence,source,source_ref);
CREATE TABLE event_pressure_watches (
    watch_id TEXT NOT NULL, source TEXT NOT NULL, source_ref TEXT NOT NULL,
    PRIMARY KEY(watch_id,source,source_ref),
    FOREIGN KEY(source,source_ref) REFERENCES event_pressure_subjects(source,source_ref)
);
CREATE INDEX event_pressure_watches_subject ON event_pressure_watches(source,source_ref);
CREATE TABLE event_pressure_hosts (
    host TEXT NOT NULL, source TEXT NOT NULL, source_ref TEXT NOT NULL,
    PRIMARY KEY(host,source,source_ref),
    FOREIGN KEY(source,source_ref) REFERENCES event_pressure_subjects(source,source_ref)
);
CREATE INDEX event_pressure_hosts_subject ON event_pressure_hosts(source,source_ref);
CREATE TABLE event_pressure_latches (
    host TEXT PRIMARY KEY, deferred INTEGER NOT NULL CHECK(deferred IN (0,1)),
    policy_digest TEXT NOT NULL, policy_json TEXT NOT NULL, changed_at TEXT NOT NULL
);
CREATE TABLE event_pressure_dirty (watch_id TEXT PRIMARY KEY);
INSERT INTO event_pressure_dirty SELECT watch_id FROM watches;
CREATE TABLE event_pressure_parents (
    watch_id TEXT NOT NULL, source TEXT NOT NULL, source_ref TEXT NOT NULL,
    PRIMARY KEY(watch_id,source,source_ref),
    FOREIGN KEY(source,source_ref) REFERENCES source_event_inventory(source,source_ref)
);
CREATE INDEX event_pressure_parents_event ON event_pressure_parents(source,source_ref);
INSERT INTO event_pressure_parents
    SELECT DISTINCT u.watch_id,i.source,i.source_ref FROM source_event_inventory i
    JOIN source_event_enumerations e USING(enumeration_id) JOIN json_each(e.parent_support_json) parent
    JOIN source_generations g ON g.generation_id=parent.key JOIN source_units u USING(unit_key);
CREATE TRIGGER pressure_inventory_insert AFTER INSERT ON source_event_inventory BEGIN
    INSERT INTO event_pressure_parents
        SELECT DISTINCT u.watch_id,NEW.source,NEW.source_ref FROM source_event_enumerations e
        JOIN json_each(e.parent_support_json) parent JOIN source_generations g ON g.generation_id=parent.key
        JOIN source_units u USING(unit_key) WHERE e.enumeration_id=NEW.enumeration_id
        ON CONFLICT(watch_id,source,source_ref) DO NOTHING;
END;

-- Dirty metadata makes new-event admission conservative until bounded enrollment.
CREATE TRIGGER pressure_watch_insert AFTER INSERT ON watches BEGIN
    INSERT INTO event_pressure_dirty VALUES (NEW.watch_id) ON CONFLICT(watch_id) DO NOTHING;
END;
CREATE TRIGGER pressure_watch_change AFTER UPDATE OF source,source_ref,url,archive_url,parser,kind ON watches BEGIN
    INSERT INTO event_pressure_dirty VALUES (NEW.watch_id) ON CONFLICT(watch_id) DO NOTHING;
    UPDATE event_pressure_subjects SET revision=revision+1 WHERE (source,source_ref) IN
        (SELECT source,source_ref FROM event_pressure_watches WHERE watch_id=NEW.watch_id);
END;
CREATE TRIGGER pressure_watch_delete AFTER DELETE ON watches BEGIN
    INSERT INTO event_pressure_dirty VALUES (OLD.watch_id) ON CONFLICT(watch_id) DO NOTHING;
    UPDATE event_pressure_subjects SET revision=revision+1 WHERE (source,source_ref) IN
        (SELECT source,source_ref FROM event_pressure_watches WHERE watch_id=OLD.watch_id);
END;
CREATE TRIGGER pressure_snapshot_insert AFTER INSERT ON snapshots BEGIN
    INSERT INTO event_pressure_dirty VALUES (NEW.watch_id) ON CONFLICT(watch_id) DO NOTHING;
    UPDATE event_pressure_subjects SET revision=revision+1 WHERE (source,source_ref) IN
        (SELECT source,source_ref FROM event_pressure_watches WHERE watch_id=NEW.watch_id);
END;
CREATE TRIGGER pressure_snapshot_change AFTER UPDATE ON snapshots BEGIN
    INSERT INTO event_pressure_dirty VALUES (NEW.watch_id) ON CONFLICT(watch_id) DO NOTHING;
    INSERT INTO event_pressure_dirty VALUES (OLD.watch_id) ON CONFLICT(watch_id) DO NOTHING;
    UPDATE event_pressure_subjects SET revision=revision+1 WHERE (source,source_ref) IN
        (SELECT source,source_ref FROM event_pressure_watches WHERE watch_id IN (OLD.watch_id,NEW.watch_id));
END;
CREATE TRIGGER pressure_snapshot_delete AFTER DELETE ON snapshots BEGIN
    INSERT INTO event_pressure_dirty VALUES (OLD.watch_id) ON CONFLICT(watch_id) DO NOTHING;
    UPDATE event_pressure_subjects SET revision=revision+1 WHERE (source,source_ref) IN
        (SELECT source,source_ref FROM event_pressure_watches WHERE watch_id=OLD.watch_id);
END;
-- A generation manifest can depend on a different watch's snapshot. Rare
-- evidence rewrites invalidate all hints; ordinary parse status updates do not.
CREATE TRIGGER pressure_snapshot_evidence AFTER UPDATE OF watch_id,method,url,form,body_sha256 ON snapshots BEGIN
    UPDATE event_pressure_state SET epoch=epoch+1;
END;
CREATE TRIGGER pressure_snapshot_removed AFTER DELETE ON snapshots BEGIN
    UPDATE event_pressure_state SET epoch=epoch+1;
END;
CREATE TRIGGER pressure_member_insert AFTER INSERT ON source_event_member_watches BEGIN
    INSERT INTO event_pressure_dirty VALUES (NEW.watch_id) ON CONFLICT(watch_id) DO NOTHING;
END;
CREATE TRIGGER pressure_enumeration_change AFTER UPDATE OF enumeration_id ON source_event_inventory BEGIN
    DELETE FROM event_pressure_parents WHERE source=NEW.source AND source_ref=NEW.source_ref;
    INSERT INTO event_pressure_parents
        SELECT DISTINCT u.watch_id,NEW.source,NEW.source_ref FROM source_event_enumerations e
        JOIN json_each(e.parent_support_json) parent JOIN source_generations g ON g.generation_id=parent.key
        JOIN source_units u USING(unit_key) WHERE e.enumeration_id=NEW.enumeration_id
        ON CONFLICT(watch_id,source,source_ref) DO NOTHING;
    UPDATE event_pressure_subjects SET revision=revision+1 WHERE source=NEW.source AND source_ref=NEW.source_ref;
    INSERT INTO event_pressure_dirty
        SELECT watch_id FROM source_event_member_watches WHERE enumeration_id=NEW.enumeration_id ON CONFLICT(watch_id) DO NOTHING;
    INSERT INTO event_pressure_dirty
        SELECT watch_id FROM event_pressure_watches WHERE source=NEW.source AND source_ref=NEW.source_ref ON CONFLICT(watch_id) DO NOTHING;
END;
CREATE TRIGGER pressure_admission_insert AFTER INSERT ON admission_decisions BEGIN
    INSERT INTO event_pressure_dirty
        SELECT u.watch_id FROM source_units u JOIN source_generations g USING(unit_key) WHERE g.generation_id=NEW.generation_id ON CONFLICT(watch_id) DO NOTHING;
    UPDATE event_pressure_subjects SET revision=revision+1 WHERE (source,source_ref) IN
        (SELECT p.source,p.source_ref FROM event_pressure_watches p JOIN source_units u USING(watch_id)
         JOIN source_generations g USING(unit_key) WHERE g.generation_id=NEW.generation_id);
    -- Shared platform indexes support events without being event requests.
    -- Watch identity also covers distinct capture units of the same parent.
    UPDATE event_pressure_subjects SET revision=revision+1 WHERE (source,source_ref) IN
        (SELECT p.source,p.source_ref FROM event_pressure_parents p
         JOIN source_units u USING(watch_id) JOIN source_generations changed USING(unit_key)
         WHERE changed.generation_id=NEW.generation_id);
END;
CREATE TRIGGER pressure_admission_change AFTER UPDATE ON admission_decisions BEGIN
    UPDATE event_pressure_state SET epoch=epoch+1;
END;
CREATE TRIGGER pressure_admission_delete AFTER DELETE ON admission_decisions BEGIN
    UPDATE event_pressure_state SET epoch=epoch+1;
END;
CREATE TRIGGER pressure_generation_state AFTER UPDATE OF state ON source_generations
WHEN NEW.state='revoked' OR OLD.state='revoked' BEGIN
    UPDATE event_pressure_state SET epoch=epoch+1;
END;
CREATE TRIGGER pressure_generation_revoked_insert AFTER INSERT ON source_generations WHEN NEW.state='revoked' BEGIN
    UPDATE event_pressure_state SET epoch=epoch+1;
END;
CREATE TRIGGER pressure_generation_delete AFTER DELETE ON source_generations BEGIN
    UPDATE event_pressure_state SET epoch=epoch+1;
END;
CREATE TRIGGER pressure_policy_insert AFTER INSERT ON admission_policies BEGIN
    UPDATE event_pressure_state SET epoch=epoch+1;
END;
CREATE TRIGGER pressure_policy_update AFTER UPDATE ON admission_policies BEGIN
    UPDATE event_pressure_state SET epoch=epoch+1;
END;
CREATE TRIGGER pressure_policy_delete AFTER DELETE ON admission_policies BEGIN
    UPDATE event_pressure_state SET epoch=epoch+1;
END;
