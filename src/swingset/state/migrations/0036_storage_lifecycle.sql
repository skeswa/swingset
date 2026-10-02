-- Storage lifecycle for phase 4 of docs/plans/architecture.md (section 9).
--
-- Derivation output, old parse bodies and large build recipes leave the
-- database for content-addressed objects under state_dir/objects/<class>/,
-- named by the sha256 of their raw bytes (state/objects.py). This migration
-- adds the tables that track them and the gate that lets their rows leave.
-- It moves no data; `swingset storage apply` does (WP3).
--
-- blob_classes holds one lifecycle policy per class of stored bytes, and
-- objects one index row per stored object. Bodies, extracts and input bundles
-- keep their own directories and get no objects rows; partition artifacts keep
-- release_artifacts as their index. dependency_set is the one class that lives
-- in this file.
CREATE TABLE blob_classes (
    class TEXT PRIMARY KEY,
    location TEXT NOT NULL,
    roots TEXT NOT NULL,
    collector TEXT NOT NULL CHECK(collector IN ('none','orphans','mark_sweep')),
    min_age_seconds INTEGER NOT NULL CHECK(min_age_seconds >= 0),
    backed_up INTEGER NOT NULL CHECK(backed_up IN (0,1))
);
INSERT INTO blob_classes(class,location,roots,collector,min_age_seconds,backed_up) VALUES
    ('body','blobs/sha256','permanent','orphans',86400,1),
    ('extract','extracts','permanent','orphans',86400,1),
    ('input_bundle','inputs','permanent','orphans',86400,1),
    ('derivation_output','objects/derivation_output','derivation_generations.output_digest','mark_sweep',86400,1),
    ('parse_body','objects/parse_body','source_generation_packs.pack_digest','mark_sweep',86400,1),
    ('label_body','objects/label_body','label_body_packs.pack_digest','mark_sweep',86400,1),
    ('release_manifest','objects/release_manifest','retained and build manifests, last 3 published','mark_sweep',86400,1),
    ('partition_artifact','partitions','release_artifacts named by a kept manifest','mark_sweep',86400,1),
    ('dependency_set','sqlite','sets named by a generation, equivalence, build recipe, candidate or building row','mark_sweep',86400,0);
CREATE TABLE objects (
    class TEXT NOT NULL REFERENCES blob_classes(class),
    digest TEXT NOT NULL,
    raw_bytes INTEGER NOT NULL CHECK(raw_bytes >= 0),
    stored_bytes INTEGER NOT NULL CHECK(stored_bytes >= 0),
    stored_at TEXT NOT NULL,
    PRIMARY KEY(class,digest)
) WITHOUT ROWID;
-- A generation whose row references left the file. Its rows are the
-- derivation_output object named by its output_digest. "Complete" means
-- resident or evicted; zero refs alone no longer means partial (F33).
CREATE TABLE derivation_evicted (
    generation_id TEXT PRIMARY KEY REFERENCES derivation_generations(generation_id),
    evicted_at TEXT NOT NULL
);
-- A source generation whose manifest, result and report went into a
-- parse_body object, and a build label whose recipe went into a label_body
-- object. The columns then hold the stub '{"packed":true}'.
CREATE TABLE source_generation_packs (
    generation_id TEXT PRIMARY KEY REFERENCES source_generations(generation_id),
    pack_digest TEXT NOT NULL,
    packed_at TEXT NOT NULL
);
CREATE INDEX source_generation_packs_digest ON source_generation_packs(pack_digest);
CREATE TABLE label_body_packs (
    generation_id TEXT PRIMARY KEY REFERENCES derivation_generations(generation_id),
    pack_digest TEXT NOT NULL,
    packed_at TEXT NOT NULL
);
CREATE INDEX label_body_packs_digest ON label_body_packs(pack_digest);
-- The collector's anti-join and the output accessor look labels up by digest.
CREATE INDEX derivation_generations_output ON derivation_generations(output_digest);
-- NULL for every set written before this migration; the collector treats
-- those as old.
ALTER TABLE derivation_dependency_sets ADD COLUMN created_at TEXT;
-- The storage gate, in the pattern of derivation_payload_removal_authority
-- (D-0136). The grant table is always empty and the authority row's deferred
-- foreign key points at it, so a transaction still holding the authority
-- cannot commit: it must delete the row first. Opening the database clears a
-- row written with foreign keys off.
CREATE TABLE storage_eviction_grant (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1)
);
CREATE TABLE storage_eviction_authority (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
    reason TEXT NOT NULL,
    granted_at TEXT NOT NULL,
    FOREIGN KEY(singleton) REFERENCES storage_eviction_grant(singleton) DEFERRABLE INITIALLY DEFERRED
);
-- Row references, dependency sets and payloads may be deleted under the
-- storage authority; everything else about them stays immutable.
DROP TRIGGER derivation_row_ref_no_delete;
CREATE TRIGGER derivation_row_ref_no_delete BEFORE DELETE ON derivation_row_refs
WHEN NOT EXISTS (SELECT 1 FROM storage_eviction_authority)
BEGIN SELECT RAISE(ABORT,'derivation rows are immutable'); END;
DROP TRIGGER derivation_dependency_no_delete;
CREATE TRIGGER derivation_dependency_no_delete BEFORE DELETE ON derivation_dependency_sets
WHEN NOT EXISTS (SELECT 1 FROM storage_eviction_authority)
BEGIN SELECT RAISE(ABORT,'derivation dependency manifests are immutable'); END;
DROP TRIGGER derivation_payload_guarded_delete;
CREATE TRIGGER derivation_payload_guarded_delete BEFORE DELETE ON derivation_payloads
WHEN NOT EXISTS (SELECT 1 FROM derivation_payload_removal_authority)
    AND NOT EXISTS (SELECT 1 FROM storage_eviction_authority)
BEGIN SELECT RAISE(ABORT,'derivation payload removal requires authority'); END;
-- Source generation evidence stays immutable except its three bodies, which
-- may change only under the authority, all three together, from values to
-- the stub (with a pack row already written) or from the stub back. One
-- trigger, under its old name, so dropping it still lifts every guard.
DROP TRIGGER source_generation_immutable;
CREATE TRIGGER source_generation_immutable BEFORE UPDATE OF unit_key,page_kind,contract_version,input_fingerprint,manifest_json,recipe_json,result_json,report_json,previous_generation_id,work_token,created_at,run_id,removal_authority ON source_generations
WHEN NOT EXISTS (SELECT 1 FROM storage_eviction_authority)
    OR NEW.unit_key IS NOT OLD.unit_key OR NEW.page_kind IS NOT OLD.page_kind
    OR NEW.contract_version IS NOT OLD.contract_version
    OR NEW.input_fingerprint IS NOT OLD.input_fingerprint
    OR NEW.recipe_json IS NOT OLD.recipe_json
    OR NEW.previous_generation_id IS NOT OLD.previous_generation_id
    OR NEW.work_token IS NOT OLD.work_token OR NEW.created_at IS NOT OLD.created_at
    OR NEW.run_id IS NOT OLD.run_id OR NEW.removal_authority IS NOT OLD.removal_authority
    OR NOT (
        (OLD.manifest_json<>'{"packed":true}' AND OLD.result_json<>'{"packed":true}'
            AND OLD.report_json<>'{"packed":true}'
            AND NEW.manifest_json='{"packed":true}' AND NEW.result_json='{"packed":true}'
            AND NEW.report_json='{"packed":true}'
            AND EXISTS (SELECT 1 FROM source_generation_packs p WHERE p.generation_id=OLD.generation_id))
        OR (OLD.manifest_json='{"packed":true}' AND OLD.result_json='{"packed":true}'
            AND OLD.report_json='{"packed":true}'
            AND NEW.manifest_json<>'{"packed":true}' AND NEW.result_json<>'{"packed":true}'
            AND NEW.report_json<>'{"packed":true}')
    )
BEGIN SELECT RAISE(ABORT,'source generation evidence is immutable'); END;
-- A derivation label stays immutable except a build label's recipe_json,
-- which may go to the stub (with a pack row) or come back, under the authority.
DROP TRIGGER derivation_generation_no_update;
CREATE TRIGGER derivation_generation_no_update BEFORE UPDATE ON derivation_generations
WHEN NOT EXISTS (SELECT 1 FROM storage_eviction_authority)
    OR NEW.generation_id IS NOT OLD.generation_id OR NEW.stage IS NOT OLD.stage
    OR NEW.unit_kind IS NOT OLD.unit_kind OR NEW.unit_id IS NOT OLD.unit_id
    OR NEW.input_fingerprint IS NOT OLD.input_fingerprint
    OR NEW.dependency_set_id IS NOT OLD.dependency_set_id
    OR NEW.previous_generation_id IS NOT OLD.previous_generation_id
    OR NEW.output_digest IS NOT OLD.output_digest OR NEW.row_count IS NOT OLD.row_count
    OR NEW.created_at IS NOT OLD.created_at OR NEW.run_id IS NOT OLD.run_id
    OR NOT (
        (OLD.stage='build' AND OLD.recipe_json<>'{"packed":true}'
            AND NEW.recipe_json='{"packed":true}'
            AND EXISTS (SELECT 1 FROM label_body_packs p WHERE p.generation_id=OLD.generation_id))
        OR (OLD.recipe_json='{"packed":true}' AND NEW.recipe_json<>'{"packed":true}')
    )
BEGIN SELECT RAISE(ABORT,'derivation generations are immutable'); END;
-- SQLite runs the newest trigger first. The migration runner reinstalls the
-- publication fences after this script, so dropping the fences of the tables
-- whose guards were recreated keeps a fence's message ahead of the guard's,
-- as before.
DROP TRIGGER IF EXISTS publication_fence_derivation_row_refs_delete;
DROP TRIGGER IF EXISTS publication_narrow_fence_derivation_row_refs_delete;
DROP TRIGGER IF EXISTS publication_fence_derivation_dependency_sets_delete;
DROP TRIGGER IF EXISTS publication_fence_derivation_payloads_delete;
DROP TRIGGER IF EXISTS publication_narrow_fence_derivation_payloads_delete;
DROP TRIGGER IF EXISTS publication_fence_source_generations_update;
DROP TRIGGER IF EXISTS publication_narrow_fence_source_generations_update;
DROP TRIGGER IF EXISTS publication_fence_derivation_generations_update;
DROP TRIGGER IF EXISTS publication_narrow_fence_derivation_generations_update;
-- The eviction marker leaves only at page-in, under the authority. A pack row
-- never changes, and leaves only under the authority once its row is restored.
CREATE TRIGGER derivation_evicted_no_update BEFORE UPDATE ON derivation_evicted
BEGIN SELECT RAISE(ABORT,'eviction markers are immutable'); END;
CREATE TRIGGER derivation_evicted_guarded_delete BEFORE DELETE ON derivation_evicted
WHEN NOT EXISTS (SELECT 1 FROM storage_eviction_authority)
BEGIN SELECT RAISE(ABORT,'eviction marker removal requires authority'); END;
CREATE TRIGGER source_generation_pack_no_update BEFORE UPDATE ON source_generation_packs
BEGIN SELECT RAISE(ABORT,'body pack rows are immutable'); END;
CREATE TRIGGER source_generation_pack_guarded_delete BEFORE DELETE ON source_generation_packs
WHEN NOT EXISTS (SELECT 1 FROM storage_eviction_authority)
    OR EXISTS (SELECT 1 FROM source_generations s WHERE s.generation_id=OLD.generation_id
        AND s.result_json='{"packed":true}')
BEGIN SELECT RAISE(ABORT,'body pack removal requires authority and a restored row'); END;
CREATE TRIGGER label_body_pack_no_update BEFORE UPDATE ON label_body_packs
BEGIN SELECT RAISE(ABORT,'body pack rows are immutable'); END;
CREATE TRIGGER label_body_pack_guarded_delete BEFORE DELETE ON label_body_packs
WHEN NOT EXISTS (SELECT 1 FROM storage_eviction_authority)
    OR EXISTS (SELECT 1 FROM derivation_generations g WHERE g.generation_id=OLD.generation_id
        AND g.recipe_json='{"packed":true}')
BEGIN SELECT RAISE(ABORT,'body pack removal requires authority and a restored row'); END;
-- One durable note per one-time storage apply and per collection apply,
-- keyed by its plan digest, written before anything moves and finished once
-- (the retention_applies pattern).
CREATE TABLE storage_applies (
    plan_digest TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK(kind IN ('storage','collect')),
    started_at TEXT NOT NULL,
    finished_at TEXT,
    receipt_json TEXT,
    CHECK((finished_at IS NULL) = (receipt_json IS NULL))
);
CREATE TRIGGER storage_apply_no_delete BEFORE DELETE ON storage_applies
BEGIN SELECT RAISE(ABORT,'storage apply notes are permanent'); END;
CREATE TRIGGER storage_apply_no_rewrite BEFORE UPDATE ON storage_applies
WHEN OLD.finished_at IS NOT NULL
    OR NEW.plan_digest IS NOT OLD.plan_digest OR NEW.kind IS NOT OLD.kind
    OR NEW.started_at IS NOT OLD.started_at
BEGIN SELECT RAISE(ABORT,'a storage apply note is written once and finished once'); END;
-- Segmented backups (WP6): one row per set, and which object keys each
-- transport already holds and in which segment.
CREATE TABLE backup_sets (
    set_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    manifest_sha256 TEXT NOT NULL,
    db_raw_bytes INTEGER NOT NULL,
    db_raw_sha256 TEXT NOT NULL,
    parts INTEGER NOT NULL,
    objects INTEGER NOT NULL,
    new_objects INTEGER NOT NULL,
    transport TEXT NOT NULL,
    uploaded_at TEXT,
    drilled_at TEXT,
    drill_json TEXT
);
CREATE TABLE backup_objects (
    transport TEXT NOT NULL,
    key TEXT NOT NULL,
    segment TEXT NOT NULL,
    staged_at TEXT NOT NULL,
    PRIMARY KEY(transport,key)
) WITHOUT ROWID;
CREATE INDEX backup_objects_segment ON backup_objects(transport,segment);
