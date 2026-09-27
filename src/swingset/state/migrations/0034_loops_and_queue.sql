-- Tables for the five loops of phase 2 (docs/plans/architecture.md, section 9).
--
-- runs.loop names the loop that owned a run; NULL for the cycle and the CLI.
-- work_queue is an overlay on pending_work and derivation currency: a row
-- exists only while a unit is leased or failing, and holds its lease, its
-- failure clock, and whether it waits for input or sits in quarantine.
-- acquisition_plan holds the plan loop's year and page rows, each with the
-- reason it may or may not be fetched. guard_reasons holds why a fetch was
-- held and when that reason should have cleared. loop_state holds each
-- loop's latest tick. derivation_building holds a generation whose rows are
-- being stored in chunks after its live write.
--
-- All five tables are mutable. None of them is an input to the history
-- dispatch proof, so none moves the dispatch fence. Publication fences are
-- installed on them by the migration runner, as on every other table. A
-- Python hook (state/queue.seed) gives each failing pending unit its row.
ALTER TABLE runs ADD COLUMN loop TEXT;
CREATE INDEX runs_loop_open ON runs(loop, finished_at);
CREATE TABLE work_queue (
    stage TEXT NOT NULL CHECK(stage IN ('parse','project','link')),
    unit_kind TEXT NOT NULL, unit_id TEXT NOT NULL,
    partition TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('ready','leased','waiting_input','quarantined')),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
    error_class TEXT, input_fingerprint TEXT,
    lease_owner TEXT, lease_until TEXT, next_attempt_at TEXT,
    last_reason TEXT, last_error TEXT,
    enqueued_at TEXT NOT NULL, updated_at TEXT NOT NULL, quarantined_at TEXT,
    PRIMARY KEY(stage,unit_kind,unit_id),
    CHECK((state='leased') = (lease_owner IS NOT NULL AND lease_until IS NOT NULL)),
    CHECK(state NOT IN ('waiting_input','quarantined') OR next_attempt_at IS NOT NULL)
);
CREATE INDEX work_queue_due ON work_queue(state, next_attempt_at);
CREATE TABLE acquisition_plan (
    plan_key TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK(kind IN ('year','page')),
    year INTEGER, source TEXT, source_ref TEXT, page_kind TEXT, watch_kind TEXT,
    url TEXT, archive_url TEXT, event_id TEXT,
    reason TEXT NOT NULL,
    rank INTEGER, detail_json TEXT,
    planned_at TEXT NOT NULL, run_id TEXT NOT NULL
);
CREATE INDEX acquisition_plan_eligible ON acquisition_plan(kind, reason, rank);
CREATE TABLE guard_reasons (
    subject TEXT NOT NULL,
    reason TEXT NOT NULL, loop TEXT NOT NULL,
    since TEXT NOT NULL, last_seen_at TEXT NOT NULL, expires_at TEXT NOT NULL,
    seen INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY(subject, reason)
);
CREATE INDEX guard_reasons_expiry ON guard_reasons(expires_at);
CREATE TABLE loop_state (
    loop TEXT PRIMARY KEY CHECK(loop IN ('acquire','plan','derive','release','maintain','cycle')),
    run_id TEXT, started_at TEXT, finished_at TEXT, failed INTEGER, error TEXT,
    counters_json TEXT NOT NULL DEFAULT '{}', last_progress_at TEXT, updated_at TEXT NOT NULL
);
CREATE TABLE derivation_building (
    generation_id TEXT PRIMARY KEY,
    stage TEXT NOT NULL, unit_kind TEXT NOT NULL, unit_id TEXT NOT NULL,
    input_fingerprint TEXT NOT NULL, witness_json TEXT,
    label_digest TEXT, label_rows INTEGER, rows_stored INTEGER NOT NULL DEFAULT 0,
    run_id TEXT NOT NULL, started_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX derivation_building_unit ON derivation_building(stage,unit_kind,unit_id);
