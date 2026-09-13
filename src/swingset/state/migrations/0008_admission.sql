-- Staged evidence is immutable; mode changes require a reviewed corpus receipt.
CREATE TABLE admission_policies (
    page_kind TEXT PRIMARY KEY,
    contract_version TEXT NOT NULL,
    mode TEXT NOT NULL DEFAULT 'shadow' CHECK(mode IN ('shadow','enforce','paused')),
    policy_revision TEXT NOT NULL,
    reviewed_report_digest TEXT,
    reviewed_by TEXT,
    reviewed_at TEXT
);
CREATE TABLE admission_reviews (
    report_digest TEXT PRIMARY KEY,
    page_kind TEXT NOT NULL,
    contract_version TEXT NOT NULL,
    cohort_json TEXT NOT NULL,
    reviewer TEXT NOT NULL,
    reviewed_at TEXT NOT NULL,
    evidence TEXT NOT NULL
);
CREATE TRIGGER admission_review_immutable BEFORE UPDATE ON admission_reviews
BEGIN SELECT RAISE(ABORT,'admission review is immutable'); END;
CREATE TABLE source_units (
    unit_key TEXT PRIMARY KEY,
    watch_id TEXT NOT NULL REFERENCES watches(watch_id),
    page_kind TEXT NOT NULL,
    desired_fingerprint TEXT,
    accepted_generation_id TEXT REFERENCES source_generations(generation_id),
    legacy_snapshot_id TEXT REFERENCES snapshots(snapshot_id),
    legacy_state TEXT NOT NULL DEFAULT 'none' CHECK(legacy_state IN ('none','legacy_unassessed','assessed'))
);
CREATE INDEX source_units_watch_idx ON source_units(watch_id);
CREATE INDEX source_units_accepted_idx ON source_units(accepted_generation_id);
CREATE INDEX source_units_legacy_idx ON source_units(legacy_snapshot_id);
CREATE TABLE source_generations (
    generation_id TEXT PRIMARY KEY,
    unit_key TEXT NOT NULL REFERENCES source_units(unit_key),
    page_kind TEXT NOT NULL,
    contract_version TEXT NOT NULL,
    input_fingerprint TEXT NOT NULL,
    manifest_json TEXT NOT NULL,
    recipe_json TEXT NOT NULL,
    result_json TEXT NOT NULL,
    report_json TEXT NOT NULL,
    previous_generation_id TEXT REFERENCES source_generations(generation_id),
    work_token TEXT,
    created_at TEXT NOT NULL,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    state TEXT NOT NULL CHECK(state IN ('staged','waiting_for_inputs','needs_review','accepted','superseded','revoked')),
    removal_authority TEXT NOT NULL CHECK(removal_authority IN ('none','watch'))
);
CREATE INDEX source_generations_unit_idx ON source_generations(unit_key,created_at,generation_id);
CREATE INDEX source_generations_previous_idx ON source_generations(previous_generation_id);
CREATE INDEX source_generations_run_idx ON source_generations(run_id);
CREATE INDEX source_generations_state_idx ON source_generations(state,page_kind);
CREATE TRIGGER source_generation_immutable BEFORE UPDATE OF unit_key,page_kind,contract_version,input_fingerprint,manifest_json,recipe_json,result_json,report_json,previous_generation_id,work_token,created_at,run_id,removal_authority ON source_generations
BEGIN SELECT RAISE(ABORT,'source generation evidence is immutable'); END;
CREATE TABLE admission_decisions (
    decision_id INTEGER PRIMARY KEY AUTOINCREMENT,
    generation_id TEXT NOT NULL REFERENCES source_generations(generation_id),
    state TEXT NOT NULL,
    reason TEXT NOT NULL,
    decided_at TEXT NOT NULL,
    policy_revision TEXT NOT NULL
);
CREATE INDEX admission_decisions_generation_idx ON admission_decisions(generation_id,decision_id);
-- Only pre-existing v1 origin output can be legacy. New history captures are
-- assessed from their first generation; migration grants no accepted pointer.
INSERT INTO source_units(unit_key,watch_id,page_kind,legacy_snapshot_id,legacy_state)
SELECT w.watch_id,w.watch_id,w.parser,w.current_observation_snapshot_id,'legacy_unassessed'
FROM watches w JOIN snapshots s ON s.snapshot_id=w.current_observation_snapshot_id
WHERE s.via='origin' AND w.source IN ('wsdc_registry','wsdc_calendar','eepro','scoringdance','wdr');
