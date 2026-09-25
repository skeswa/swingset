-- Record that a re-run matched a generation already kept, without minting a new one.
--
-- Phase 1 of docs/plans/architecture.md lets a derivation or parse re-run whose
-- output is identical keep the generation it already has (output cutoff). The
-- re-run's own input fingerprint and recipe still have to be on record, so a
-- later reader can tell the kept generation is current for them. These tables
-- hold that fact. The migration itself writes no rows; derivations.complete
-- and admission.generations.stage_generation write equivalences
-- (journal/decisions/0205-cut-off-identical-derivation-output.md,
-- journal/decisions/0206-add-migration-33-after-interning.md).
--
-- An equivalence's recipe is nearly the same across a unit kind (every dancer
-- shares one), so derivation equivalences name it by digest in
-- derivation_recipes. Parse recipes carry snapshot context, so source
-- equivalences keep theirs inline, as source_generations does.
CREATE TABLE derivation_recipes (
    recipe_sha256 TEXT PRIMARY KEY,
    recipe_json TEXT NOT NULL
);
CREATE TABLE derivation_equivalences (
    stage TEXT NOT NULL, unit_kind TEXT NOT NULL, unit_id TEXT NOT NULL,
    input_fingerprint TEXT NOT NULL,
    generation_id TEXT NOT NULL REFERENCES derivation_generations(generation_id),
    recipe_sha256 TEXT NOT NULL REFERENCES derivation_recipes(recipe_sha256),
    dependency_set_id TEXT NOT NULL REFERENCES derivation_dependency_sets(dependency_set_id),
    created_at TEXT NOT NULL, run_id TEXT NOT NULL REFERENCES runs(run_id),
    PRIMARY KEY(stage,unit_kind,unit_id,input_fingerprint,generation_id),
    FOREIGN KEY(stage,unit_kind,unit_id) REFERENCES derivation_scopes(stage,unit_kind,unit_id)
);
CREATE INDEX derivation_equivalences_generation ON derivation_equivalences(generation_id);
CREATE INDEX derivation_equivalences_dependencies ON derivation_equivalences(dependency_set_id);
CREATE INDEX derivation_equivalences_run ON derivation_equivalences(run_id);
CREATE INDEX derivation_equivalences_recipe ON derivation_equivalences(recipe_sha256);
CREATE TABLE source_equivalences (
    unit_key TEXT NOT NULL REFERENCES source_units(unit_key),
    input_fingerprint TEXT NOT NULL,
    generation_id TEXT NOT NULL REFERENCES source_generations(generation_id),
    recipe_json TEXT NOT NULL, work_token TEXT,
    created_at TEXT NOT NULL, run_id TEXT NOT NULL REFERENCES runs(run_id),
    PRIMARY KEY(unit_key,input_fingerprint,generation_id)
);
CREATE INDEX source_equivalences_generation ON source_equivalences(generation_id);
CREATE INDEX source_equivalences_run ON source_equivalences(run_id);
-- Equivalences are immutable evidence; invalidation step 3 collects the ones
-- no current input names (D-0206).
CREATE TRIGGER derivation_recipe_no_update BEFORE UPDATE ON derivation_recipes
BEGIN SELECT RAISE(ABORT,'derivation recipes are immutable'); END;
CREATE TRIGGER derivation_recipe_no_delete BEFORE DELETE ON derivation_recipes
BEGIN SELECT RAISE(ABORT,'derivation recipes are immutable'); END;
CREATE TRIGGER derivation_equivalence_no_update BEFORE UPDATE ON derivation_equivalences
BEGIN SELECT RAISE(ABORT,'derivation equivalences are immutable'); END;
CREATE TRIGGER derivation_equivalence_no_delete BEFORE DELETE ON derivation_equivalences
BEGIN SELECT RAISE(ABORT,'derivation equivalences are immutable'); END;
CREATE TRIGGER source_equivalence_no_update BEFORE UPDATE ON source_equivalences
BEGIN SELECT RAISE(ABORT,'source equivalences are immutable'); END;
CREATE TRIGGER source_equivalence_no_delete BEFORE DELETE ON source_equivalences
BEGIN SELECT RAISE(ABORT,'source equivalences are immutable'); END;
-- The history dispatch fence watches every derivation and source record.
CREATE TRIGGER history_timing_derivation_recipes_insert AFTER INSERT ON derivation_recipes BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_derivation_recipes_update AFTER UPDATE ON derivation_recipes BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_derivation_recipes_delete AFTER DELETE ON derivation_recipes BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_derivation_equivalences_insert AFTER INSERT ON derivation_equivalences BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_derivation_equivalences_update AFTER UPDATE ON derivation_equivalences BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_derivation_equivalences_delete AFTER DELETE ON derivation_equivalences BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_source_equivalences_insert AFTER INSERT ON source_equivalences BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_source_equivalences_update AFTER UPDATE ON source_equivalences BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_source_equivalences_delete AFTER DELETE ON source_equivalences BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
-- A watch token certifies what derivations read from source_units: unit_key,
-- accepted_generation_id and legacy_snapshot_id, found by watch_id. Staging a
-- parse rewrites desired_fingerprint every time, and no derivation reads it,
-- so that write no longer bumps the token. Once parse cutoff stops moving the
-- accepted pointer, a bump on every parse would send each dependent project
-- unit down the slow currentness path forever.
DROP TRIGGER derivation_source_units_update;
CREATE TRIGGER derivation_source_units_update AFTER UPDATE ON source_units
WHEN OLD.watch_id IS NOT NEW.watch_id OR OLD.unit_key IS NOT NEW.unit_key
    OR OLD.accepted_generation_id IS NOT NEW.accepted_generation_id
    OR OLD.legacy_snapshot_id IS NOT NEW.legacy_snapshot_id
BEGIN INSERT INTO derivation_input_versions VALUES ('watch',OLD.watch_id,1) ON CONFLICT(namespace,input_key) DO UPDATE SET version=version+1; INSERT INTO derivation_input_versions VALUES ('watch',NEW.watch_id,1) ON CONFLICT(namespace,input_key) DO UPDATE SET version=version+1; END;
-- The ('source_selections','all') token, which source_index and inventory
-- certificates read, bumped on every source_units update for the same reason.
DROP TRIGGER derivation_source_selections_update;
CREATE TRIGGER derivation_source_selections_update AFTER UPDATE ON source_units
WHEN OLD.watch_id IS NOT NEW.watch_id OR OLD.unit_key IS NOT NEW.unit_key
    OR OLD.accepted_generation_id IS NOT NEW.accepted_generation_id
    OR OLD.legacy_snapshot_id IS NOT NEW.legacy_snapshot_id
BEGIN INSERT INTO derivation_input_versions VALUES ('source_selections','all',1) ON CONFLICT(namespace,input_key) DO UPDATE SET version=version+1; END;
