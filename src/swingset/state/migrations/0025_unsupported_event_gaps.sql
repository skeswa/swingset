-- Unsupported observations inspect generation reports, including unadmitted ones.
-- Keep this domain fence separate from successful progress qualification.
CREATE TRIGGER gap_generation_insert AFTER INSERT ON source_generations BEGIN
    INSERT INTO event_gap_revisions
    SELECT w.source,1 FROM source_units u JOIN watches w USING(watch_id)
    WHERE u.unit_key=NEW.unit_key
    ON CONFLICT(source) DO UPDATE SET revision=revision+1;
END;
CREATE TRIGGER gap_generation_update AFTER UPDATE ON source_generations BEGIN
    INSERT INTO event_gap_revisions
    SELECT DISTINCT w.source,1 FROM source_units u JOIN watches w USING(watch_id)
    WHERE u.unit_key IN (OLD.unit_key,NEW.unit_key)
    ON CONFLICT(source) DO UPDATE SET revision=revision+1;
END;
CREATE TRIGGER gap_generation_delete AFTER DELETE ON source_generations BEGIN
    INSERT INTO event_gap_revisions
    SELECT w.source,1 FROM source_units u JOIN watches w USING(watch_id)
    WHERE u.unit_key=OLD.unit_key
    ON CONFLICT(source) DO UPDATE SET revision=revision+1;
END;
CREATE TRIGGER gap_unit_watch_change AFTER UPDATE OF watch_id ON source_units
WHEN OLD.watch_id IS NOT NEW.watch_id BEGIN
    INSERT INTO event_gap_revisions
    SELECT DISTINCT source,1 FROM watches WHERE watch_id IN (OLD.watch_id,NEW.watch_id)
    ON CONFLICT(source) DO UPDATE SET revision=revision+1;
END;
