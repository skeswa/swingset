-- Tables for phase 3 of docs/plans/architecture.md (section 9): link read
-- sets, per-source mapping proposals, and partitioned releases.
--
-- derivation_reads is the reverse index: the read keys of each link unit's
-- current generation, replaced at completion or cutoff. A read key is
-- 'project/<kind>/<id>', 'content:<name>/<id>' or 'pool:<event>'.
-- source_event_proposals holds the target each project/mapping/<source> unit
-- proposes for a listing of its source, with the placeholder event it would
-- create when nothing matches. release_partitions, release_artifacts and
-- release_manifests are the lifecycle of partition artifacts and release
-- manifests; the sidecars and manifests themselves are files under
-- state_dir/partitions/, not rows.
--
-- None of the five tables is a dispatch proof input, so none moves the
-- history dispatch fence. Publication fences are installed on them by the
-- migration runner, as on every other table.
--
-- project/history/all and project/map/all are retired (per-year and
-- per-source units replace them): their pending and queue rows go, and so
-- do their building rows, which nothing would ever finish (a history/all
-- staged at the switch; its partial generation stays, counted with the
-- other partial generations until phase 4). Their derivation generations
-- stay. The years re-run at the switch and own the live rows it wrote.
CREATE TABLE derivation_reads (
    stage TEXT NOT NULL, unit_kind TEXT NOT NULL, unit_id TEXT NOT NULL,
    read_key TEXT NOT NULL,
    PRIMARY KEY(stage,unit_kind,unit_id,read_key)
) WITHOUT ROWID;
CREATE INDEX derivation_reads_key ON derivation_reads(read_key);
CREATE TABLE source_event_proposals (
    source TEXT NOT NULL, source_ref TEXT NOT NULL,
    event_id TEXT NOT NULL, match_method TEXT NOT NULL, match_confidence REAL NOT NULL,
    placeholder_json TEXT,
    PRIMARY KEY(source, source_ref)
);
CREATE INDEX source_event_proposals_event ON source_event_proposals(event_id);
CREATE TABLE release_partitions (
    partition TEXT PRIMARY KEY,
    inputs_digest TEXT NOT NULL, artifact_digest TEXT,
    state TEXT NOT NULL CHECK(state IN ('built','withheld')),
    reason TEXT, detail_json TEXT, built_at TEXT NOT NULL, run_id TEXT NOT NULL,
    CHECK((state='built') = (artifact_digest IS NOT NULL)),
    CHECK(state='built' OR reason IS NOT NULL)
);
CREATE TABLE release_artifacts (
    artifact_digest TEXT PRIMARY KEY, partition TEXT NOT NULL,
    bytes INTEGER NOT NULL, created_at TEXT NOT NULL, last_named_at TEXT NOT NULL
);
CREATE TABLE release_manifests (
    manifest_digest TEXT PRIMARY KEY, bytes INTEGER NOT NULL, created_at TEXT NOT NULL,
    candidate_id TEXT, verdict_json TEXT
);
DELETE FROM pending_work
WHERE stage='project' AND unit_kind IN ('history','map') AND unit_id='all';
DELETE FROM work_queue
WHERE stage='project' AND unit_kind IN ('history','map') AND unit_id='all';
DELETE FROM derivation_building
WHERE stage='project' AND unit_kind IN ('history','map') AND unit_id='all';
