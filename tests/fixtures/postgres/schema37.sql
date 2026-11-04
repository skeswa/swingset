BEGIN TRANSACTION;
CREATE TABLE accepted_inputs (consumer TEXT NOT NULL, input_name TEXT NOT NULL, digest TEXT NOT NULL, PRIMARY KEY(consumer, input_name));
CREATE TABLE acquisition_plan (
    plan_key TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK(kind IN ('year','page')),
    year INTEGER, source TEXT, source_ref TEXT, page_kind TEXT, watch_kind TEXT,
    url TEXT, archive_url TEXT, event_id TEXT,
    reason TEXT NOT NULL,
    rank INTEGER, detail_json TEXT,
    planned_at TEXT NOT NULL, run_id TEXT NOT NULL
);
CREATE TABLE admission_decisions (
    decision_id INTEGER PRIMARY KEY AUTOINCREMENT,
    generation_id TEXT NOT NULL REFERENCES source_generations(generation_id),
    state TEXT NOT NULL,
    reason TEXT NOT NULL,
    decided_at TEXT NOT NULL,
    policy_revision TEXT NOT NULL
);
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
CREATE TABLE archive_captures (source TEXT NOT NULL,url TEXT NOT NULL,timestamp TEXT NOT NULL,digest TEXT NOT NULL,status INTEGER NOT NULL,mimetype TEXT NOT NULL,length INTEGER NOT NULL,queried_at TEXT NOT NULL,cdx_query_id TEXT NOT NULL,PRIMARY KEY(source,url,timestamp));
CREATE TABLE archive_queries (query_id TEXT PRIMARY KEY,source TEXT NOT NULL,prefix TEXT NOT NULL,year INTEGER NOT NULL,next_page INTEGER NOT NULL DEFAULT 0,total_pages INTEGER,completed_at TEXT,started_at TEXT NOT NULL DEFAULT '');
CREATE TABLE backup_objects (
    transport TEXT NOT NULL,
    key TEXT NOT NULL,
    segment TEXT NOT NULL,
    staged_at TEXT NOT NULL,
    PRIMARY KEY(transport,key)
) WITHOUT ROWID;
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
CREATE TABLE backup_uploads (path TEXT PRIMARY KEY, sha256 TEXT NOT NULL, uploaded_at TEXT NOT NULL);
CREATE TABLE blob_classes (
    class TEXT PRIMARY KEY,
    location TEXT NOT NULL,
    roots TEXT NOT NULL,
    collector TEXT NOT NULL CHECK(collector IN ('none','orphans','mark_sweep')),
    min_age_seconds INTEGER NOT NULL CHECK(min_age_seconds >= 0),
    backed_up INTEGER NOT NULL CHECK(backed_up IN (0,1))
);
INSERT INTO "blob_classes" VALUES('body','blobs/sha256','permanent','orphans',86400,1);
INSERT INTO "blob_classes" VALUES('extract','extracts','permanent','orphans',86400,1);
INSERT INTO "blob_classes" VALUES('input_bundle','inputs','permanent','orphans',86400,1);
INSERT INTO "blob_classes" VALUES('derivation_output','objects/derivation_output','derivation_generations.output_digest','mark_sweep',86400,1);
INSERT INTO "blob_classes" VALUES('parse_body','objects/parse_body','source_generation_packs.pack_digest','mark_sweep',86400,1);
INSERT INTO "blob_classes" VALUES('label_body','objects/label_body','label_body_packs.pack_digest','mark_sweep',86400,1);
INSERT INTO "blob_classes" VALUES('release_manifest','objects/release_manifest','retained and build manifests, last 3 published','mark_sweep',86400,1);
INSERT INTO "blob_classes" VALUES('partition_artifact','partitions','release_artifacts named by a kept manifest','mark_sweep',86400,1);
INSERT INTO "blob_classes" VALUES('dependency_set','sqlite','sets named by a generation, equivalence, build recipe, candidate or building row','mark_sweep',86400,0);
CREATE TABLE callback_marks (round_id TEXT NOT NULL REFERENCES rounds(round_id) ON DELETE CASCADE, entry_id TEXT NOT NULL REFERENCES entries(entry_id) ON DELETE CASCADE, judge_id TEXT NOT NULL REFERENCES judges(judge_id) ON DELETE CASCADE, mark TEXT NOT NULL, mark_raw TEXT NOT NULL, mark_value REAL NOT NULL, source TEXT NOT NULL, snapshot_id TEXT NOT NULL, parser_version TEXT NOT NULL, first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, run_id TEXT NOT NULL, PRIMARY KEY(round_id, entry_id, judge_id));
CREATE TABLE callbacks (round_id TEXT NOT NULL REFERENCES rounds(round_id) ON DELETE CASCADE, entry_id TEXT NOT NULL REFERENCES entries(entry_id) ON DELETE CASCADE, score_sum REAL NOT NULL, yes_count INTEGER NOT NULL, alt_count INTEGER NOT NULL, no_count INTEGER NOT NULL, outcome TEXT NOT NULL, tie_break_applied INTEGER, heat_number INTEGER, source TEXT NOT NULL, snapshot_id TEXT NOT NULL, parser_version TEXT NOT NULL, first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, run_id TEXT NOT NULL, PRIMARY KEY(round_id, entry_id));
CREATE TABLE canonical_scope_rows (scope_kind TEXT NOT NULL, scope_id TEXT NOT NULL, table_name TEXT NOT NULL, record_key TEXT NOT NULL, PRIMARY KEY(scope_kind, scope_id, table_name, record_key));
CREATE TABLE contests (contest_id TEXT PRIMARY KEY, event_id TEXT NOT NULL REFERENCES events(event_id) ON DELETE CASCADE, name_raw TEXT NOT NULL, division TEXT NOT NULL, age_division TEXT NOT NULL, contest_type TEXT NOT NULL, partner_mode TEXT NOT NULL, dance_style TEXT NOT NULL, wsdc_points_eligible INTEGER NOT NULL, combined_from TEXT NOT NULL, parse_status TEXT NOT NULL, source_contest_ref TEXT NOT NULL, source TEXT NOT NULL, snapshot_id TEXT NOT NULL, parser_version TEXT NOT NULL, first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, run_id TEXT NOT NULL);
CREATE TABLE control_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    control_revision INTEGER NOT NULL, pause_id TEXT,
    scope_kind TEXT NOT NULL, scope_id TEXT NOT NULL,
    action TEXT NOT NULL CHECK(action IN ('pause','resume','expire','legacy_import')),
    actor TEXT NOT NULL, reason TEXT NOT NULL, until_at TEXT, occurred_at TEXT NOT NULL
);
CREATE TABLE control_state (singleton INTEGER PRIMARY KEY CHECK(singleton=1), revision INTEGER NOT NULL);
INSERT INTO "control_state" VALUES(1,0);
CREATE TABLE coverage (year INTEGER NOT NULL,source TEXT NOT NULL,via TEXT NOT NULL,events INTEGER NOT NULL DEFAULT 0,contests INTEGER NOT NULL DEFAULT 0,rounds INTEGER NOT NULL DEFAULT 0,entries INTEGER NOT NULL DEFAULT 0,events_registry_only INTEGER NOT NULL DEFAULT 0,events_index_only INTEGER NOT NULL DEFAULT 0,events_sheets_partial INTEGER NOT NULL DEFAULT 0,events_sheets_complete INTEGER NOT NULL DEFAULT 0,events_day_precision INTEGER NOT NULL DEFAULT 0,events_listed_only INTEGER NOT NULL DEFAULT 0,events_accepted INTEGER NOT NULL DEFAULT 0,expected_rounds INTEGER,parsed_rounds INTEGER NOT NULL DEFAULT 0,unresolved_findings INTEGER NOT NULL DEFAULT 0,last_changed_at TEXT NOT NULL,PRIMARY KEY(year,source,via));
CREATE TABLE cursors (name TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE dancers (wsdc_id INTEGER PRIMARY KEY, first_name TEXT NOT NULL, last_name TEXT NOT NULL, name_norm TEXT NOT NULL, is_pro INTEGER NOT NULL, primary_role TEXT NOT NULL, leader_required_level TEXT NOT NULL, leader_allowed_level TEXT NOT NULL, follower_required_level TEXT NOT NULL, follower_allowed_level TEXT NOT NULL, leader_highest_level TEXT NOT NULL, leader_highest_points INTEGER NOT NULL, follower_highest_level TEXT NOT NULL, follower_highest_points INTEGER NOT NULL, recent_year INTEGER NOT NULL, registry_internal_id INTEGER NOT NULL, registry_fetched_at TEXT NOT NULL, merged_into_wsdc_id INTEGER, source TEXT NOT NULL, snapshot_id TEXT NOT NULL, parser_version TEXT NOT NULL, first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, run_id TEXT NOT NULL);
CREATE TABLE derivation_building (
    generation_id TEXT PRIMARY KEY,
    stage TEXT NOT NULL, unit_kind TEXT NOT NULL, unit_id TEXT NOT NULL,
    input_fingerprint TEXT NOT NULL, witness_json TEXT,
    label_digest TEXT, label_rows INTEGER, rows_stored INTEGER NOT NULL DEFAULT 0,
    run_id TEXT NOT NULL, started_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE derivation_dependency_sets (
    dependency_set_id TEXT PRIMARY KEY, manifest_json TEXT NOT NULL
, created_at TEXT);
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
CREATE TABLE derivation_evicted (
    generation_id TEXT PRIMARY KEY REFERENCES derivation_generations(generation_id),
    evicted_at TEXT NOT NULL
);
CREATE TABLE derivation_generations (
    generation_id TEXT PRIMARY KEY,
    stage TEXT NOT NULL, unit_kind TEXT NOT NULL, unit_id TEXT NOT NULL,
    input_fingerprint TEXT NOT NULL, recipe_json TEXT NOT NULL,
    dependency_set_id TEXT NOT NULL REFERENCES derivation_dependency_sets(dependency_set_id),
    previous_generation_id TEXT REFERENCES derivation_generations(generation_id),
    output_digest TEXT NOT NULL, row_count INTEGER NOT NULL,
    created_at TEXT NOT NULL, run_id TEXT NOT NULL REFERENCES runs(run_id),
    FOREIGN KEY(stage,unit_kind,unit_id) REFERENCES derivation_scopes(stage,unit_kind,unit_id)
);
CREATE TABLE derivation_input_versions (
    namespace TEXT NOT NULL, input_key TEXT NOT NULL, version INTEGER NOT NULL,
    PRIMARY KEY(namespace,input_key)
);
CREATE TABLE derivation_payload_removal_authority (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
    reason TEXT NOT NULL,
    granted_at TEXT NOT NULL,
    FOREIGN KEY(singleton) REFERENCES derivation_payload_removal_grant(singleton) DEFERRABLE INITIALLY DEFERRED
);
CREATE TABLE derivation_payload_removal_grant (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1)
);
CREATE TABLE derivation_payloads (
    payload_sha256 TEXT PRIMARY KEY,
    payload_json TEXT NOT NULL
);
CREATE TABLE derivation_reads (
    stage TEXT NOT NULL, unit_kind TEXT NOT NULL, unit_id TEXT NOT NULL,
    read_key TEXT NOT NULL,
    PRIMARY KEY(stage,unit_kind,unit_id,read_key)
) WITHOUT ROWID;
CREATE TABLE derivation_recipes (
    recipe_sha256 TEXT PRIMARY KEY,
    recipe_json TEXT NOT NULL
);
CREATE TABLE derivation_row_refs (
    generation_id TEXT NOT NULL REFERENCES derivation_generations(generation_id) DEFERRABLE INITIALLY DEFERRED,
    ordinal INTEGER NOT NULL, table_name TEXT NOT NULL, record_key TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    PRIMARY KEY(generation_id,ordinal),
    UNIQUE(generation_id,table_name,record_key)
);
CREATE TABLE derivation_scopes (
    stage TEXT NOT NULL, unit_kind TEXT NOT NULL, unit_id TEXT NOT NULL,
    desired_fingerprint TEXT, materialized_generation_id TEXT REFERENCES derivation_generations(generation_id),
    registered_at TEXT NOT NULL, materialized_signature TEXT, PRIMARY KEY(stage,unit_kind,unit_id)
);
CREATE TABLE entries (entry_id TEXT PRIMARY KEY, contest_id TEXT NOT NULL REFERENCES contests(contest_id) ON DELETE CASCADE, event_id TEXT NOT NULL REFERENCES events(event_id) ON DELETE CASCADE, role TEXT NOT NULL, bib TEXT, name_raw TEXT, name_norm TEXT, partner_name_raw TEXT, city_raw TEXT, country_raw TEXT, wsdc_id INTEGER, link_status TEXT NOT NULL DEFAULT 'unmatched', link_confidence REAL NOT NULL DEFAULT 0, partner_entry_id TEXT, rounds_danced TEXT NOT NULL, best_round TEXT, source TEXT NOT NULL, snapshot_id TEXT NOT NULL, parser_version TEXT NOT NULL, first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, run_id TEXT NOT NULL);
CREATE TABLE event_accounting_receipts (
    receipt_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL, source_ref TEXT NOT NULL, enumeration_id TEXT,
    previous_receipt_id INTEGER REFERENCES event_accounting_receipts(receipt_id),
    assessment TEXT NOT NULL CHECK(assessment IN ('locally_accounted','unfinished','unassessed')),
    transition TEXT NOT NULL CHECK(transition IN ('initial_observation','membership_changed','reopened','availability_restored','assessment_changed')),
    observed_at TEXT NOT NULL, earliest_checked_at TEXT, valid_until TEXT,
    token_json TEXT NOT NULL, summary_json TEXT NOT NULL
);
CREATE TABLE event_accounting_support_observations (
    source TEXT NOT NULL, source_ref TEXT NOT NULL,
    enumeration_id TEXT NOT NULL REFERENCES source_event_enumerations(enumeration_id),
    generation_id TEXT NOT NULL,
    availability INTEGER CHECK(availability IN (0,1)),
    observed_at TEXT NOT NULL, valid_until TEXT NOT NULL,
    token_json TEXT NOT NULL, reasons_json TEXT NOT NULL,
    PRIMARY KEY(source,source_ref,generation_id)
);
CREATE TABLE event_blocker_cursor (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1), last_rowid INTEGER NOT NULL DEFAULT 0,
    high_water INTEGER NOT NULL DEFAULT 0, pass INTEGER NOT NULL DEFAULT 0
);
INSERT INTO "event_blocker_cursor" VALUES(1,0,0,0);
CREATE TABLE event_blocker_latest (
    source TEXT NOT NULL, source_ref TEXT NOT NULL,
    observation_id INTEGER NOT NULL REFERENCES event_blocker_observations(observation_id),
    PRIMARY KEY(source,source_ref)
);
CREATE TABLE event_blocker_observations (
    observation_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL, source_ref TEXT NOT NULL,
    refresh_id INTEGER NOT NULL REFERENCES event_blocker_refreshes(refresh_id),
    observed_at TEXT NOT NULL, signature TEXT NOT NULL,
    policy_digest TEXT NOT NULL REFERENCES event_blocker_policies(digest), facts_json TEXT NOT NULL
);
CREATE TABLE event_blocker_policies (digest TEXT PRIMARY KEY, policy_json TEXT NOT NULL);
CREATE TABLE event_blocker_refreshes (
    refresh_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(run_id), observed_at TEXT NOT NULL,
    summary_json TEXT NOT NULL
);
CREATE TABLE event_enumeration_inputs (
    generation_id TEXT PRIMARY KEY REFERENCES source_generations(generation_id),
    decision_id INTEGER NOT NULL REFERENCES admission_decisions(decision_id),
    outcome TEXT NOT NULL, recorded_at TEXT NOT NULL
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
CREATE TABLE event_gap_revisions (
    source TEXT PRIMARY KEY, revision INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE event_pressure_dirty (watch_id TEXT PRIMARY KEY);
CREATE TABLE event_pressure_hosts (
    host TEXT NOT NULL, source TEXT NOT NULL, source_ref TEXT NOT NULL,
    PRIMARY KEY(host,source,source_ref),
    FOREIGN KEY(source,source_ref) REFERENCES event_pressure_subjects(source,source_ref)
);
CREATE TABLE event_pressure_latches (
    host TEXT PRIMARY KEY, deferred INTEGER NOT NULL CHECK(deferred IN (0,1)),
    policy_digest TEXT NOT NULL, policy_json TEXT NOT NULL, changed_at TEXT NOT NULL
);
CREATE TABLE event_pressure_parents (
    watch_id TEXT NOT NULL, source TEXT NOT NULL, source_ref TEXT NOT NULL,
    PRIMARY KEY(watch_id,source,source_ref),
    FOREIGN KEY(source,source_ref) REFERENCES source_event_inventory(source,source_ref)
);
CREATE TABLE event_pressure_state (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1), epoch INTEGER NOT NULL DEFAULT 0,
    sequence INTEGER NOT NULL DEFAULT 0
);
INSERT INTO "event_pressure_state" VALUES(1,0,0);
CREATE TABLE event_pressure_subjects (
    source TEXT NOT NULL, source_ref TEXT NOT NULL, enrolled_at TEXT NOT NULL,
    start_basis TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0,
    checked_sequence INTEGER NOT NULL DEFAULT 0, scan_json TEXT, observation_json TEXT,
    PRIMARY KEY(source,source_ref)
);
CREATE TABLE event_pressure_watches (
    watch_id TEXT NOT NULL, source TEXT NOT NULL, source_ref TEXT NOT NULL,
    PRIMARY KEY(watch_id,source,source_ref),
    FOREIGN KEY(source,source_ref) REFERENCES event_pressure_subjects(source,source_ref)
);
CREATE TABLE event_progress_cursor (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1), last_rowid INTEGER NOT NULL DEFAULT 0,
    high_water INTEGER NOT NULL DEFAULT 0
);
INSERT INTO "event_progress_cursor" VALUES(1,0,0);
CREATE TABLE event_progress_observations (
    source TEXT NOT NULL, source_ref TEXT NOT NULL, request_id TEXT NOT NULL,
    stage TEXT NOT NULL CHECK(stage IN ('acquired','interpreted')),
    enumeration_id TEXT NOT NULL REFERENCES source_event_enumerations(enumeration_id),
    observed_at TEXT NOT NULL, valid_until TEXT NOT NULL,
    availability INTEGER CHECK(availability IN (0,1)),
    operation_frontier INTEGER NOT NULL, token_json TEXT NOT NULL,
    reasons_json TEXT NOT NULL,
    PRIMARY KEY(source,source_ref,request_id,stage)
);
CREATE TABLE event_progress_policies(digest TEXT PRIMARY KEY, policy_json TEXT NOT NULL);
CREATE TABLE event_progress_receipts (
    receipt_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL, source_ref TEXT NOT NULL, request_id TEXT NOT NULL,
    stage TEXT NOT NULL CHECK(stage IN ('acquired','interpreted')),
    enumeration_id TEXT NOT NULL REFERENCES source_event_enumerations(enumeration_id),
    operation_id INTEGER NOT NULL REFERENCES event_stage_operations(operation_id),
    missing_observed_at TEXT NOT NULL, verified_at TEXT NOT NULL,
    token_json TEXT NOT NULL,
    UNIQUE(source,source_ref,request_id,stage,operation_id)
);
CREATE TABLE event_progress_scans (
    source TEXT NOT NULL, source_ref TEXT NOT NULL,
    enumeration_id TEXT, after_request_id TEXT, assessment_reason TEXT, assessed_at TEXT NOT NULL,
    PRIMARY KEY(source,source_ref)
);
CREATE TABLE event_retirement_observations (
    source TEXT NOT NULL, source_ref TEXT NOT NULL,
    enumeration_id TEXT NOT NULL REFERENCES source_event_enumerations(enumeration_id),
    predecessor_id TEXT REFERENCES source_event_enumerations(enumeration_id),
    assessment TEXT NOT NULL CHECK(assessment IN ('verified','unassessed','not_applicable')),
    observed_at TEXT NOT NULL, valid_until TEXT NOT NULL,
    token_json TEXT NOT NULL, result_json TEXT NOT NULL,
    retry_fresh INTEGER NOT NULL CHECK(retry_fresh IN (0,1)),
    PRIMARY KEY(source,source_ref)
);
CREATE TABLE event_retirement_receipts (
    receipt_id INTEGER PRIMARY KEY AUTOINCREMENT,
    proof_digest TEXT NOT NULL UNIQUE,
    source TEXT NOT NULL, source_ref TEXT NOT NULL,
    enumeration_id TEXT NOT NULL, predecessor_id TEXT NOT NULL,
    generation_id TEXT NOT NULL, decision_id INTEGER NOT NULL,
    observed_at TEXT NOT NULL, policy_digest TEXT NOT NULL,
    proof_json TEXT NOT NULL
);
CREATE TABLE event_stage_operations (
    operation_id INTEGER PRIMARY KEY AUTOINCREMENT,
    stage TEXT NOT NULL CHECK(stage IN ('acquired','interpreted')),
    source TEXT NOT NULL, request_id TEXT, watch_id TEXT NOT NULL,
    snapshot_id TEXT, generation_id TEXT, decision_id INTEGER,
    occurred_at TEXT NOT NULL, run_id TEXT NOT NULL REFERENCES runs(run_id),
    CHECK((stage='acquired' AND snapshot_id IS NOT NULL AND request_id IS NOT NULL AND generation_id IS NULL AND decision_id IS NULL)
       OR (stage='interpreted' AND generation_id IS NOT NULL AND decision_id IS NOT NULL AND snapshot_id IS NULL)),
    UNIQUE(snapshot_id), UNIQUE(generation_id)
);
CREATE TABLE event_timing (
    source TEXT NOT NULL, source_ref TEXT NOT NULL,
    episode_id TEXT NOT NULL, token_json TEXT NOT NULL, policy_json TEXT NOT NULL,
    observation_origin TEXT NOT NULL, saved_at TEXT NOT NULL,
    run_id TEXT NOT NULL, phase_open INTEGER NOT NULL,
    eligible_seconds REAL NOT NULL DEFAULT 0, blocked_seconds REAL NOT NULL DEFAULT 0,
    unknown_seconds REAL NOT NULL DEFAULT 0, inactive_seconds REAL NOT NULL DEFAULT 0,
    since_service_seconds REAL NOT NULL DEFAULT 0, since_progress_seconds REAL NOT NULL DEFAULT 0,
    complete_coverage INTEGER NOT NULL DEFAULT 1,
    service_bound_valid INTEGER NOT NULL DEFAULT 1, progress_bound_valid INTEGER NOT NULL DEFAULT 1,
    reason_seconds_json TEXT NOT NULL DEFAULT '{}',
    state TEXT NOT NULL, reason TEXT NOT NULL, valid_until TEXT NOT NULL,
    service_frontier INTEGER NOT NULL, progress_frontier INTEGER NOT NULL, operation_origin INTEGER NOT NULL,
    last_progress_at TEXT, last_service_action TEXT, last_service_at TEXT,
    unresolved_success INTEGER NOT NULL DEFAULT 0,
    control_revision INTEGER NOT NULL, marker_mtime_ns INTEGER NOT NULL,
    PRIMARY KEY(source,source_ref)
);
CREATE TABLE event_timing_cursor (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1), last_rowid INTEGER NOT NULL DEFAULT 0,
    high_water INTEGER NOT NULL DEFAULT 0
);
INSERT INTO "event_timing_cursor" VALUES(1,0,0);
CREATE TABLE event_timing_history (
    episode_id TEXT NOT NULL, run_id TEXT NOT NULL, source TEXT NOT NULL, source_ref TEXT NOT NULL,
    summary_json TEXT NOT NULL,
    PRIMARY KEY(episode_id,run_id)
);
CREATE TABLE "events" (event_id TEXT PRIMARY KEY, series_id TEXT NOT NULL, name TEXT NOT NULL, year INTEGER NOT NULL, start_date TEXT, end_date TEXT, city TEXT, region TEXT, country TEXT, website TEXT, wsdc_status TEXT NOT NULL, sources TEXT NOT NULL, live_window_start TEXT, live_window_end TEXT, source TEXT NOT NULL, snapshot_id TEXT NOT NULL, parser_version TEXT NOT NULL, first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, run_id TEXT NOT NULL, event_month TEXT NOT NULL DEFAULT '', date_precision TEXT NOT NULL DEFAULT 'day', held TEXT NOT NULL DEFAULT 'listed', coverage_tier TEXT NOT NULL DEFAULT 'index_only', history_source TEXT NOT NULL DEFAULT '[]');
CREATE TABLE execution_admissions (
    action_id TEXT PRIMARY KEY, action_kind TEXT NOT NULL,
    admitted_at TEXT NOT NULL, control_revision INTEGER NOT NULL,
    run_id TEXT REFERENCES runs(run_id), work_attempt_id INTEGER REFERENCES work_attempts(attempt_id),
    candidate_id TEXT, host TEXT, all_sources INTEGER NOT NULL, all_kinds INTEGER NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('active','uncertain','settled')),
    settled_at TEXT, outcome TEXT
);
CREATE TABLE "execution_dependencies" (
    action_id TEXT NOT NULL REFERENCES execution_admissions(action_id),
    scope_kind TEXT NOT NULL CHECK(scope_kind IN ('source','kind')),
    scope_id TEXT NOT NULL, PRIMARY KEY(action_id,scope_kind,scope_id)
);
CREATE TABLE final_marks (round_id TEXT NOT NULL REFERENCES rounds(round_id) ON DELETE CASCADE, placement_id TEXT NOT NULL REFERENCES placements(placement_id) ON DELETE CASCADE, judge_id TEXT NOT NULL REFERENCES judges(judge_id) ON DELETE CASCADE, rank INTEGER NOT NULL, source TEXT NOT NULL, snapshot_id TEXT NOT NULL, parser_version TEXT NOT NULL, first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, run_id TEXT NOT NULL, PRIMARY KEY(round_id, placement_id, judge_id));
CREATE TABLE finding_support (finding_id TEXT PRIMARY KEY,payload_json TEXT NOT NULL,active INTEGER NOT NULL);
CREATE TABLE finding_support_references (
    finding_id TEXT NOT NULL REFERENCES findings(finding_id) DEFERRABLE INITIALLY DEFERRED,
    kind TEXT NOT NULL CHECK(kind IN ('body','extract','generation')),
    sha256 TEXT NOT NULL,
    PRIMARY KEY(finding_id,kind,sha256)
);
CREATE TABLE findings (
 finding_id TEXT PRIMARY KEY, owner_kind TEXT NOT NULL, owner_id TEXT NOT NULL, kind TEXT NOT NULL, subject_kind TEXT NOT NULL,
 subject_id TEXT NOT NULL, watch_id TEXT REFERENCES watches(watch_id), snapshot_id TEXT REFERENCES snapshots(snapshot_id), severity TEXT NOT NULL,
 summary TEXT NOT NULL, evidence_json TEXT NOT NULL, suggested_override TEXT, opened_at TEXT NOT NULL, run_id TEXT NOT NULL REFERENCES runs(run_id),
 closed_at TEXT, closed_by TEXT
, policy_version TEXT NOT NULL DEFAULT '1', desired_fingerprint TEXT NOT NULL DEFAULT '', state TEXT NOT NULL DEFAULT 'needs_review', source TEXT, next_action TEXT NOT NULL DEFAULT 'review', blocking_reason TEXT, next_eligible_at TEXT, attempt_count INTEGER NOT NULL DEFAULT 0, last_progress_at TEXT, status_at TEXT);
CREATE TABLE guard_reasons (
    subject TEXT NOT NULL,
    reason TEXT NOT NULL, loop TEXT NOT NULL,
    since TEXT NOT NULL, last_seen_at TEXT NOT NULL, expires_at TEXT NOT NULL,
    seen INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY(subject, reason)
);
CREATE TABLE heats (heat_id TEXT NOT NULL, round_id TEXT NOT NULL REFERENCES rounds(round_id) ON DELETE CASCADE, heat_number INTEGER NOT NULL, entry_id TEXT NOT NULL REFERENCES entries(entry_id) ON DELETE CASCADE, position INTEGER, source TEXT NOT NULL, snapshot_id TEXT NOT NULL, parser_version TEXT NOT NULL, first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, run_id TEXT NOT NULL, PRIMARY KEY(heat_id, entry_id));
CREATE TABLE history_acceptance (year INTEGER PRIMARY KEY,accepted_at TEXT NOT NULL,accepted_by TEXT NOT NULL,inventory_digest TEXT NOT NULL);
CREATE TABLE history_dispatch_fence (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1), revision INTEGER NOT NULL
);
INSERT INTO "history_dispatch_fence" VALUES(1,9);
CREATE TABLE history_origin_intents (
    watch_id TEXT PRIMARY KEY REFERENCES watches(watch_id),
    source TEXT NOT NULL, source_ref TEXT NOT NULL, event_id TEXT NOT NULL,
    page_kind TEXT NOT NULL, url TEXT NOT NULL, parent_url TEXT,
    evidence_json TEXT NOT NULL, created_at TEXT NOT NULL,
    dispatch_run_id TEXT REFERENCES runs(run_id)
);
CREATE TABLE history_origin_operator_refs (
    source_ref TEXT PRIMARY KEY,
    evidence_reference TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);
CREATE TABLE history_origin_requests (
    action_id TEXT PRIMARY KEY REFERENCES execution_admissions(action_id),
    watch_id TEXT NOT NULL REFERENCES watches(watch_id),
    source TEXT NOT NULL, event_id TEXT NOT NULL,
    run_id TEXT NOT NULL REFERENCES runs(run_id), day TEXT NOT NULL,
    issued_at TEXT NOT NULL
);
CREATE TABLE host_budget (host TEXT NOT NULL, day TEXT NOT NULL, requests INTEGER NOT NULL DEFAULT 0, bytes INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(host, day));
CREATE TABLE host_request_spacing (
    host TEXT PRIMARY KEY REFERENCES hosts(host),
    reservation_id TEXT NOT NULL UNIQUE,
    gap_seconds REAL CHECK (gap_seconds >= 2),
    reserved_at TEXT,
    released_at TEXT
);
CREATE TABLE host_request_spacing_baselines (
    reservation_id TEXT PRIMARY KEY,
    host TEXT NOT NULL,
    gap_seconds REAL NOT NULL CHECK (gap_seconds >= 5),
    stopped_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    evidence_ref TEXT NOT NULL,
    prior_reservation_id TEXT
);
CREATE TABLE hosts (host TEXT PRIMARY KEY, next_allowed_at TEXT, paused_until TEXT, pause_reason TEXT, pause_streak INTEGER NOT NULL DEFAULT 0, robots_sha256 TEXT, robots_fetched_at TEXT, robots_status INTEGER);
CREATE TABLE identity_decisions (
    decision_id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    source_event TEXT NOT NULL,
    contest TEXT NOT NULL,
    round TEXT NOT NULL,
    participant TEXT NOT NULL,
    wsdc_id TEXT NOT NULL,
    decision TEXT NOT NULL CHECK(decision IN ('same_person','different_person','insufficient_evidence','hold_unlinked')),
    evidence TEXT NOT NULL,
    reason TEXT NOT NULL,
    author TEXT NOT NULL,
    date TEXT NOT NULL,
    supersedes TEXT REFERENCES identity_decisions(decision_id),
    row_json TEXT NOT NULL,
    row_sha256 TEXT NOT NULL,
    accepted_digest TEXT NOT NULL,
    accepted_at TEXT NOT NULL
);
CREATE TABLE identity_journal_acceptances (
    digest TEXT PRIMARY KEY,
    bundle_digest TEXT NOT NULL,
    accepted_at TEXT NOT NULL,
    decision_count INTEGER NOT NULL,
    generation INTEGER NOT NULL
);
CREATE TABLE identity_link_history (
    history_id INTEGER PRIMARY KEY,
    subject_kind TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('accepted','unresolved','revoked','superseded')),
    assertion_json TEXT NOT NULL,
    resolution_json TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);
CREATE TABLE identity_link_resolutions (
    subject_kind TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    ref_ids_json TEXT NOT NULL,
    decision_ids_json TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    journal_digest TEXT NOT NULL,
    journal_generation INTEGER NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('accepted','unresolved','revoked','superseded')),
    reason TEXT NOT NULL,
    asserted_at TEXT NOT NULL,
    PRIMARY KEY(subject_kind,subject_id)
);
CREATE TABLE identity_links (link_id TEXT PRIMARY KEY, subject_kind TEXT NOT NULL, subject_id TEXT NOT NULL, wsdc_id INTEGER, method TEXT NOT NULL, status TEXT NOT NULL, confidence REAL NOT NULL, constraints_applied TEXT NOT NULL, asserted_at TEXT NOT NULL, run_id TEXT NOT NULL, linker_version TEXT NOT NULL, UNIQUE(subject_kind, subject_id));
CREATE TABLE identity_reference_bindings (
    binding_id TEXT PRIMARY KEY,
    ref_id TEXT NOT NULL REFERENCES identity_source_refs(ref_id),
    subject_kind TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    snapshot_id TEXT NOT NULL,
    semantic_hash TEXT NOT NULL,
    bound_at TEXT NOT NULL
);
CREATE TABLE identity_reference_migrations (
    migration_id TEXT PRIMARY KEY,
    from_ref_id TEXT NOT NULL REFERENCES identity_source_refs(ref_id),
    to_ref_ids_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('approved','pending','ambiguous')),
    evidence TEXT NOT NULL,
    reason TEXT NOT NULL,
    author TEXT NOT NULL,
    date TEXT NOT NULL,
    supersedes TEXT REFERENCES identity_reference_migrations(migration_id),
    recorded_at TEXT NOT NULL
);
CREATE TABLE identity_source_refs (
    ref_id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    source_event TEXT NOT NULL,
    contest TEXT NOT NULL,
    round TEXT NOT NULL,
    participant TEXT NOT NULL,
    subject_kind TEXT NOT NULL CHECK(subject_kind IN ('entry','judge')),
    locator_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(source,source_event,contest,round,participant)
);
CREATE TABLE judges (judge_id TEXT PRIMARY KEY, event_id TEXT NOT NULL REFERENCES events(event_id) ON DELETE CASCADE, name_raw TEXT, initials TEXT, anonymous INTEGER NOT NULL, wsdc_id INTEGER, source TEXT NOT NULL, snapshot_id TEXT NOT NULL, parser_version TEXT NOT NULL, first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, run_id TEXT NOT NULL);
CREATE TABLE label_body_packs (
    generation_id TEXT PRIMARY KEY REFERENCES derivation_generations(generation_id),
    pack_digest TEXT NOT NULL,
    packed_at TEXT NOT NULL
);
CREATE TABLE "link_candidates" (
    subject_kind TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    wsdc_id INTEGER NOT NULL,
    score REAL NOT NULL,
    name_similarity REAL NOT NULL,
    name_rarity REAL NOT NULL,
    division_ok INTEGER,
    role_ok INTEGER,
    recency_ok INTEGER NOT NULL,
    geography REAL,
    bib_reuse INTEGER NOT NULL,
    registry_confirms INTEGER NOT NULL,
    source_id_confirms INTEGER NOT NULL,
    rank INTEGER NOT NULL,
    chosen INTEGER NOT NULL,
    run_id TEXT NOT NULL,
    PRIMARY KEY (subject_kind, subject_id, wsdc_id)
);
CREATE TABLE loop_state (
    loop TEXT PRIMARY KEY CHECK(loop IN ('acquire','plan','derive','release','maintain','cycle')),
    run_id TEXT, started_at TEXT, finished_at TEXT, failed INTEGER, error TEXT,
    counters_json TEXT NOT NULL DEFAULT '{}', last_progress_at TEXT, updated_at TEXT NOT NULL
);
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
INSERT INTO "meta" VALUES('schema_version','37');
INSERT INTO "meta" VALUES('installed_at','2026-11-04T22:01:00.357Z');
INSERT INTO "meta" VALUES('registry_verification_migrated','1');
INSERT INTO "meta" VALUES('identity_journal_digest','');
CREATE TABLE objects (
    class TEXT NOT NULL REFERENCES blob_classes(class),
    digest TEXT NOT NULL,
    raw_bytes INTEGER NOT NULL CHECK(raw_bytes >= 0),
    stored_bytes INTEGER NOT NULL CHECK(stored_bytes >= 0),
    stored_at TEXT NOT NULL,
    PRIMARY KEY(class,digest)
) WITHOUT ROWID;
CREATE TABLE observations (
 observation_id TEXT PRIMARY KEY, watch_id TEXT NOT NULL REFERENCES watches(watch_id), snapshot_id TEXT NOT NULL REFERENCES snapshots(snapshot_id),
 kind TEXT NOT NULL, scope_kind TEXT NOT NULL, scope_id TEXT NOT NULL, seq INTEGER NOT NULL,
 extract_version TEXT NOT NULL, parser_version TEXT NOT NULL, payload_json TEXT NOT NULL,
 UNIQUE(watch_id, snapshot_id, kind, seq)
);
CREATE TABLE operator_pauses (scope_kind TEXT NOT NULL, scope_id TEXT NOT NULL, until_at TEXT, reason TEXT NOT NULL, pause_id TEXT, actor TEXT, control_revision INTEGER NOT NULL DEFAULT 0, created_at TEXT, PRIMARY KEY(scope_kind, scope_id));
CREATE TABLE pending_work (stage TEXT NOT NULL, unit_kind TEXT NOT NULL, unit_id TEXT NOT NULL, enqueued_at TEXT NOT NULL, PRIMARY KEY(stage, unit_kind, unit_id));
CREATE TABLE placements (placement_id TEXT PRIMARY KEY, round_id TEXT NOT NULL REFERENCES rounds(round_id) ON DELETE CASCADE, contest_id TEXT NOT NULL REFERENCES contests(contest_id) ON DELETE CASCADE, event_id TEXT NOT NULL REFERENCES events(event_id) ON DELETE CASCADE, place INTEGER NOT NULL, leader_entry_id TEXT, follower_entry_id TEXT, couple_entry_id TEXT, leader_wsdc_id INTEGER, follower_wsdc_id INTEGER, marks_sorted TEXT, tally TEXT NOT NULL, registry_points_leader INTEGER, registry_points_follower INTEGER, registry_confirmed INTEGER NOT NULL DEFAULT 0, points_matches_expected INTEGER, source TEXT NOT NULL, snapshot_id TEXT NOT NULL, parser_version TEXT NOT NULL, first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, run_id TEXT NOT NULL);
CREATE TABLE registry_placements (wsdc_id INTEGER NOT NULL REFERENCES dancers(wsdc_id) ON DELETE CASCADE, role TEXT NOT NULL, dance_style TEXT NOT NULL, division TEXT NOT NULL, series_id TEXT NOT NULL, series_name_raw TEXT NOT NULL, event_month TEXT NOT NULL, event_id TEXT REFERENCES events(event_id), result TEXT NOT NULL, points INTEGER NOT NULL, source TEXT NOT NULL, snapshot_id TEXT NOT NULL, parser_version TEXT NOT NULL, first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, run_id TEXT NOT NULL, PRIMARY KEY(wsdc_id, role, series_id, event_month, division, dance_style));
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
CREATE TABLE release_artifacts (
    artifact_digest TEXT PRIMARY KEY, partition TEXT NOT NULL,
    bytes INTEGER NOT NULL, created_at TEXT NOT NULL, last_named_at TEXT NOT NULL
);
CREATE TABLE release_manifests (
    manifest_digest TEXT PRIMARY KEY, bytes INTEGER NOT NULL, created_at TEXT NOT NULL,
    candidate_id TEXT, verdict_json TEXT
);
CREATE TABLE release_partitions (
    partition TEXT PRIMARY KEY,
    inputs_digest TEXT NOT NULL, artifact_digest TEXT,
    state TEXT NOT NULL CHECK(state IN ('built','withheld')),
    reason TEXT, detail_json TEXT, built_at TEXT NOT NULL, run_id TEXT NOT NULL,
    CHECK((state='built') = (artifact_digest IS NOT NULL)),
    CHECK(state='built' OR reason IS NOT NULL)
);
CREATE TABLE requirement_scan (singleton INTEGER PRIMARY KEY CHECK(singleton=1),cursor TEXT NOT NULL DEFAULT '',started_at TEXT,last_scanned_at TEXT,last_completed_at TEXT,scanned INTEGER NOT NULL DEFAULT 0);
INSERT INTO "requirement_scan" VALUES(1,'',NULL,NULL,NULL,0);
CREATE TABLE retention_applies (
    plan_digest TEXT PRIMARY KEY,
    planned_files_json TEXT NOT NULL,
    planned_payloads_json TEXT NOT NULL,
    removed_payload_bytes INTEGER NOT NULL,
    started_at TEXT NOT NULL,
    files_completed_at TEXT,
    receipt_json TEXT
);
CREATE TABLE revisions (name TEXT PRIMARY KEY, value INTEGER NOT NULL DEFAULT 0);
INSERT INTO "revisions" VALUES('observations',0);
INSERT INTO "revisions" VALUES('source_event_map',0);
INSERT INTO "revisions" VALUES('source_events',0);
INSERT INTO "revisions" VALUES('canonical',0);
INSERT INTO "revisions" VALUES('dancers',0);
INSERT INTO "revisions" VALUES('links',0);
INSERT INTO "revisions" VALUES('findings',0);
INSERT INTO "revisions" VALUES('snapshots',0);
INSERT INTO "revisions" VALUES('identity_decisions',0);
CREATE TABLE rounds (round_id TEXT PRIMARY KEY, contest_id TEXT NOT NULL REFERENCES contests(contest_id) ON DELETE CASCADE, round_type TEXT NOT NULL, round_index INTEGER NOT NULL, name_raw TEXT NOT NULL, scoring_method TEXT NOT NULL, callback_legend TEXT NOT NULL, judge_count INTEGER NOT NULL, chief_judge_id TEXT, entry_count INTEGER NOT NULL, promoted_count INTEGER, source_round_ref TEXT NOT NULL, score_sheet_url TEXT, source TEXT NOT NULL, snapshot_id TEXT NOT NULL, parser_version TEXT NOT NULL, first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, run_id TEXT NOT NULL);
CREATE TABLE runs (run_id TEXT PRIMARY KEY, started_at TEXT NOT NULL, finished_at TEXT, dry_run INTEGER NOT NULL CHECK(dry_run IN (0,1)), summary_json TEXT, loop TEXT);
CREATE TABLE scheduler_capacity_policies (
    digest TEXT PRIMARY KEY, policy_json TEXT NOT NULL
);
CREATE TABLE scheduler_capacity_requests (
    action_id TEXT PRIMARY KEY REFERENCES scheduler_requests(action_id),
    selected_host TEXT NOT NULL,
    lane TEXT NOT NULL CHECK(lane IN ('listed_result','discovery')),
    purpose TEXT NOT NULL,
    reason TEXT NOT NULL CHECK(reason IN ('competing','borrowed')),
    policy_digest TEXT NOT NULL REFERENCES scheduler_capacity_policies(digest),
    credit_before INTEGER NOT NULL, credit_after INTEGER NOT NULL
);
CREATE TABLE scheduler_capacity_service (
    selected_host TEXT PRIMARY KEY,
    credit INTEGER NOT NULL DEFAULT 0 CHECK(credit BETWEEN -8000 AND 8000)
);
CREATE TABLE scheduler_event_policies (
    digest TEXT PRIMARY KEY, policy_json TEXT NOT NULL
);
CREATE TABLE scheduler_event_requests (
    action_id TEXT PRIMARY KEY REFERENCES scheduler_requests(action_id),
    selected_host TEXT NOT NULL, category TEXT NOT NULL, owner_key TEXT NOT NULL,
    source TEXT, source_ref TEXT,
    enumeration_id TEXT REFERENCES source_event_enumerations(enumeration_id),
    turn_position INTEGER NOT NULL,
    policy_digest TEXT NOT NULL REFERENCES scheduler_event_policies(digest),
    reason TEXT NOT NULL
);
CREATE TABLE scheduler_event_turns (
    host TEXT NOT NULL, category TEXT NOT NULL, owner_key TEXT NOT NULL,
    source TEXT, source_ref TEXT,
    position INTEGER NOT NULL, used INTEGER NOT NULL DEFAULT 0 CHECK(used>=0),
    policy_digest TEXT NOT NULL REFERENCES scheduler_event_policies(digest),
    target_requests INTEGER NOT NULL CHECK(target_requests BETWEEN 1 AND 64),
    enrolled_at TEXT NOT NULL, last_issued_at TEXT,
    PRIMARY KEY(host,category,owner_key), UNIQUE(host,category,position)
);
CREATE TABLE scheduler_offline_service (
    stage TEXT NOT NULL, unit_kind TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0, last_served_at TEXT NOT NULL,
    sequence INTEGER NOT NULL, PRIMARY KEY(stage,unit_kind)
);
CREATE TABLE scheduler_parent_links (
    parent_watch_id TEXT NOT NULL REFERENCES watches(watch_id),
    child_watch_id TEXT NOT NULL REFERENCES watches(watch_id),
    first_snapshot_id TEXT REFERENCES snapshots(snapshot_id), first_seen_at TEXT,
    PRIMARY KEY(parent_watch_id,child_watch_id)
);
CREATE TABLE scheduler_requests (
    action_id TEXT PRIMARY KEY REFERENCES execution_admissions(action_id),
    host TEXT NOT NULL, day TEXT NOT NULL, category TEXT NOT NULL,
    work_key TEXT NOT NULL, run_id TEXT REFERENCES runs(run_id),
    repair INTEGER NOT NULL DEFAULT 0, pressure_reserved INTEGER NOT NULL DEFAULT 0,
    issued_at TEXT NOT NULL,
    body_bytes INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE scheduler_watch_state (
    watch_id TEXT PRIMARY KEY REFERENCES watches(watch_id),
    metadata_attempts INTEGER NOT NULL DEFAULT 0, last_metadata_attempt_at TEXT
);
CREATE TABLE series (series_id TEXT PRIMARY KEY,name TEXT NOT NULL,website TEXT,location_raw TEXT);
CREATE TABLE snapshots (
 snapshot_id TEXT PRIMARY KEY, watch_id TEXT NOT NULL REFERENCES watches(watch_id), method TEXT NOT NULL, url TEXT NOT NULL,
 form TEXT, fetched_at TEXT NOT NULL, http_status INTEGER NOT NULL, etag TEXT, last_modified TEXT, content_type TEXT,
 body_sha256 TEXT, body_bytes INTEGER NOT NULL, content_changed INTEGER NOT NULL CHECK(content_changed IN (0,1)), run_id TEXT NOT NULL REFERENCES runs(run_id),
 via TEXT NOT NULL DEFAULT 'origin', headers_json TEXT NOT NULL DEFAULT '{}', classification TEXT NOT NULL,
 extract_status TEXT, extract_sha256 TEXT, parse_status TEXT, parsed_at TEXT, extract_version TEXT, parser_version TEXT
, captured_at TEXT, observed_at TEXT, archive_url TEXT, requested_archive_url TEXT, extract_recipe_sha256 TEXT);
CREATE TABLE source_equivalences (
    unit_key TEXT NOT NULL REFERENCES source_units(unit_key),
    input_fingerprint TEXT NOT NULL,
    generation_id TEXT NOT NULL REFERENCES source_generations(generation_id),
    recipe_json TEXT NOT NULL, work_token TEXT,
    created_at TEXT NOT NULL, run_id TEXT NOT NULL REFERENCES runs(run_id),
    PRIMARY KEY(unit_key,input_fingerprint,generation_id)
);
CREATE TABLE source_event_enumeration_members (
    enumeration_id TEXT NOT NULL REFERENCES source_event_enumerations(enumeration_id),
    request_id TEXT NOT NULL, request_json TEXT NOT NULL,
    support_json TEXT NOT NULL, first_known_at TEXT,
    PRIMARY KEY(enumeration_id,request_id)
);
CREATE TABLE source_event_enumerations (
    enumeration_id TEXT PRIMARY KEY,
    source TEXT NOT NULL, source_ref TEXT NOT NULL,
    predecessor_id TEXT REFERENCES source_event_enumerations(enumeration_id),
    generation_id TEXT NOT NULL REFERENCES source_generations(generation_id),
    decision_id INTEGER NOT NULL REFERENCES admission_decisions(decision_id),
    parent_support_json TEXT NOT NULL,
    membership_digest TEXT NOT NULL,
    pagination TEXT NOT NULL CHECK(pagination IN ('unknown','complete')),
    pagination_reason TEXT NOT NULL,
    added_json TEXT NOT NULL, removed_json TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);
CREATE TABLE source_event_inventory (
    source TEXT NOT NULL, source_ref TEXT NOT NULL,
    enumeration_id TEXT REFERENCES source_event_enumerations(enumeration_id),
    first_known_at TEXT, bootstrap_basis TEXT NOT NULL,
    PRIMARY KEY(source,source_ref)
);
CREATE TABLE source_event_map (source TEXT NOT NULL, source_ref TEXT NOT NULL, event_id TEXT NOT NULL, match_method TEXT NOT NULL, match_confidence REAL NOT NULL, PRIMARY KEY(source, source_ref));
CREATE TABLE source_event_member_watches (
    enumeration_id TEXT NOT NULL, request_id TEXT NOT NULL, watch_id TEXT NOT NULL,
    PRIMARY KEY(enumeration_id,request_id,watch_id),
    FOREIGN KEY(enumeration_id,request_id) REFERENCES source_event_enumeration_members(enumeration_id,request_id)
);
CREATE TABLE source_event_proposals (
    source TEXT NOT NULL, source_ref TEXT NOT NULL,
    event_id TEXT NOT NULL, match_method TEXT NOT NULL, match_confidence REAL NOT NULL,
    placeholder_json TEXT,
    PRIMARY KEY(source, source_ref)
);
CREATE TABLE source_event_retirement_proofs (
    proof_digest TEXT PRIMARY KEY, proof_json TEXT NOT NULL
);
CREATE TABLE source_event_retirement_receipts (
    receipt_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL, source_ref TEXT NOT NULL,
    enumeration_id TEXT NOT NULL, predecessor_id TEXT NOT NULL,
    generation_id TEXT NOT NULL, decision_id INTEGER NOT NULL,
    observed_at TEXT NOT NULL, policy_digest TEXT NOT NULL,
    proof_digest TEXT NOT NULL, proof_json TEXT NOT NULL,
    UNIQUE(source,source_ref,enumeration_id)
);
CREATE TABLE source_event_scope_rows (scope_id TEXT NOT NULL, source TEXT NOT NULL, source_ref TEXT NOT NULL, PRIMARY KEY(scope_id, source, source_ref));
CREATE TABLE source_events (
 source TEXT NOT NULL, source_ref TEXT NOT NULL, name_raw TEXT, start_date TEXT, end_date TEXT, location_raw TEXT, url TEXT NOT NULL,
 snapshot_id TEXT NOT NULL, parser_version TEXT NOT NULL, first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, run_id TEXT NOT NULL,
 PRIMARY KEY(source, source_ref)
);
CREATE TABLE source_generation_packs (
    generation_id TEXT PRIMARY KEY REFERENCES source_generations(generation_id),
    pack_digest TEXT NOT NULL,
    packed_at TEXT NOT NULL
);
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
CREATE TABLE source_units (
    unit_key TEXT PRIMARY KEY,
    watch_id TEXT NOT NULL REFERENCES watches(watch_id),
    page_kind TEXT NOT NULL,
    desired_fingerprint TEXT,
    accepted_generation_id TEXT REFERENCES source_generations(generation_id),
    legacy_snapshot_id TEXT REFERENCES snapshots(snapshot_id),
    legacy_state TEXT NOT NULL DEFAULT 'none' CHECK(legacy_state IN ('none','legacy_unassessed','assessed'))
);
DELETE FROM "sqlite_sequence";
INSERT INTO "sqlite_sequence" VALUES('control_events',0);
CREATE TABLE storage_applies (
    plan_digest TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK(kind IN ('storage','collect')),
    started_at TEXT NOT NULL,
    finished_at TEXT,
    receipt_json TEXT,
    CHECK((finished_at IS NULL) = (receipt_json IS NULL))
);
CREATE TABLE storage_eviction_authority (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
    reason TEXT NOT NULL,
    granted_at TEXT NOT NULL,
    FOREIGN KEY(singleton) REFERENCES storage_eviction_grant(singleton) DEFERRABLE INITIALLY DEFERRED
);
CREATE TABLE storage_eviction_grant (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1)
);
CREATE TABLE watches (
 watch_id TEXT PRIMARY KEY, source TEXT NOT NULL, kind TEXT NOT NULL, method TEXT NOT NULL, url TEXT NOT NULL,
 form TEXT, archive_url TEXT, parser TEXT NOT NULL, source_ref TEXT, state TEXT NOT NULL, next_check_at TEXT,
 last_checked_at TEXT, last_changed_at TEXT, unchanged_streak INTEGER NOT NULL DEFAULT 0, etag TEXT, last_modified TEXT,
 body_sha256 TEXT, paused_until TEXT, notes TEXT, fingerprint TEXT, extract_version TEXT, priority INTEGER NOT NULL DEFAULT 0,
 created_by_snapshot_id TEXT, parent_watch_id TEXT REFERENCES watches(watch_id), ever_ok INTEGER NOT NULL DEFAULT 0,
 current_observation_snapshot_id TEXT, consecutive_404s INTEGER NOT NULL DEFAULT 0, first_404_at TEXT
);
CREATE TABLE work_attempts (
    attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,
    stage TEXT NOT NULL, unit_kind TEXT NOT NULL, unit_id TEXT NOT NULL,
    queue_generation INTEGER NOT NULL, retry_generation INTEGER NOT NULL,
    work_token TEXT NOT NULL, input_fingerprint TEXT NOT NULL,
    run_id TEXT NOT NULL REFERENCES runs(run_id), started_at TEXT NOT NULL,
    finished_at TEXT,
    outcome TEXT NOT NULL CHECK(outcome IN ('running','succeeded','blocked','transient','unavailable','interrupted','superseded')),
    reason_code TEXT, evidence_json TEXT NOT NULL DEFAULT '{}', retry_at TEXT,
    requirement_id TEXT
);
CREATE TABLE work_generations (
    stage TEXT NOT NULL, unit_kind TEXT NOT NULL, unit_id TEXT NOT NULL,
    generation INTEGER NOT NULL, retry_generation INTEGER NOT NULL DEFAULT 0,
    retry_reason TEXT, retry_requested_at TEXT,
    PRIMARY KEY(stage,unit_kind,unit_id)
);
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
CREATE INDEX watches_due_idx ON watches(state, priority DESC, next_check_at);
CREATE INDEX snapshots_watch_idx ON snapshots(watch_id, fetched_at, snapshot_id);
CREATE INDEX snapshots_parser_idx ON snapshots(parser_version, extract_version);
CREATE INDEX observations_watch_idx ON observations(watch_id);
CREATE INDEX observations_scope_idx ON observations(scope_kind, scope_id);
CREATE INDEX source_event_map_event_idx ON source_event_map(event_id);
CREATE INDEX pending_work_order_idx ON pending_work(stage, enqueued_at, unit_kind, unit_id);
CREATE INDEX findings_owner_idx ON findings(owner_kind, owner_id, closed_at);
CREATE INDEX contests_event_idx ON contests(event_id);
CREATE INDEX rounds_contest_idx ON rounds(contest_id);
CREATE INDEX entries_event_idx ON entries(event_id);
CREATE INDEX judges_event_idx ON judges(event_id);
CREATE INDEX registry_verification_watch ON registry_verifications(watch_id, checked_at);
CREATE INDEX canonical_scope_rows_record_idx ON canonical_scope_rows(table_name,record_key);
CREATE INDEX snapshots_body_idx ON snapshots(body_sha256,snapshot_id);
CREATE INDEX events_series_month_idx ON events(series_id,event_month);
CREATE INDEX registry_placements_occurrence_idx ON registry_placements(series_id,event_month,event_id);
CREATE INDEX placements_leader_entry_idx ON placements(leader_entry_id);
CREATE INDEX placements_follower_entry_idx ON placements(follower_entry_id);
CREATE INDEX identity_links_method_wsdc_idx ON identity_links(method,wsdc_id);
CREATE INDEX watches_kind_watch_idx ON watches(kind,watch_id);
CREATE INDEX finding_support_active_idx ON finding_support(active,finding_id);
CREATE INDEX findings_owner_finding_idx ON findings(owner_kind,finding_id);
CREATE INDEX callback_marks_judge_id_fk_idx ON callback_marks(judge_id);
CREATE INDEX callback_marks_entry_id_fk_idx ON callback_marks(entry_id);
CREATE INDEX callbacks_entry_id_fk_idx ON callbacks(entry_id);
CREATE INDEX entries_contest_id_fk_idx ON entries(contest_id);
CREATE INDEX final_marks_judge_id_fk_idx ON final_marks(judge_id);
CREATE INDEX final_marks_placement_id_fk_idx ON final_marks(placement_id);
CREATE INDEX findings_run_id_fk_idx ON findings(run_id);
CREATE INDEX findings_snapshot_id_fk_idx ON findings(snapshot_id);
CREATE INDEX findings_watch_id_fk_idx ON findings(watch_id);
CREATE INDEX heats_entry_id_fk_idx ON heats(entry_id);
CREATE INDEX heats_round_id_fk_idx ON heats(round_id);
CREATE INDEX observations_snapshot_id_fk_idx ON observations(snapshot_id);
CREATE INDEX placements_event_id_fk_idx ON placements(event_id);
CREATE INDEX placements_contest_id_fk_idx ON placements(contest_id);
CREATE INDEX placements_round_id_fk_idx ON placements(round_id);
CREATE INDEX registry_placements_event_id_fk_idx ON registry_placements(event_id);
CREATE INDEX registry_verifications_snapshot_id_fk_idx ON registry_verifications(snapshot_id);
CREATE INDEX snapshots_run_id_fk_idx ON snapshots(run_id);
CREATE INDEX watches_parent_watch_id_fk_idx ON watches(parent_watch_id);
CREATE TRIGGER admission_review_immutable BEFORE UPDATE ON admission_reviews
BEGIN SELECT RAISE(ABORT,'admission review is immutable'); END;
CREATE INDEX source_units_watch_idx ON source_units(watch_id);
CREATE INDEX source_units_accepted_idx ON source_units(accepted_generation_id);
CREATE INDEX source_units_legacy_idx ON source_units(legacy_snapshot_id);
CREATE INDEX source_generations_unit_idx ON source_generations(unit_key,created_at,generation_id);
CREATE INDEX source_generations_previous_idx ON source_generations(previous_generation_id);
CREATE INDEX source_generations_run_idx ON source_generations(run_id);
CREATE INDEX source_generations_state_idx ON source_generations(state,page_kind);
CREATE INDEX admission_decisions_generation_idx ON admission_decisions(generation_id,decision_id);
CREATE INDEX identity_decisions_reference_idx ON identity_decisions(source,source_event,contest,round,participant);
CREATE INDEX identity_decisions_supersedes_idx ON identity_decisions(supersedes);
CREATE INDEX identity_reference_bindings_subject_idx ON identity_reference_bindings(subject_kind,subject_id);
CREATE INDEX identity_reference_bindings_ref_idx ON identity_reference_bindings(ref_id);
CREATE INDEX identity_reference_migrations_from_idx ON identity_reference_migrations(from_ref_id);
CREATE INDEX identity_reference_migrations_supersedes_idx ON identity_reference_migrations(supersedes);
CREATE INDEX identity_link_history_subject_idx ON identity_link_history(subject_kind,subject_id,history_id);
CREATE TRIGGER identity_decisions_no_update BEFORE UPDATE ON identity_decisions BEGIN SELECT RAISE(ABORT,'identity decisions are append-only'); END;
CREATE TRIGGER identity_decisions_no_delete BEFORE DELETE ON identity_decisions BEGIN SELECT RAISE(ABORT,'identity decisions are append-only'); END;
CREATE TRIGGER identity_acceptances_no_update BEFORE UPDATE ON identity_journal_acceptances BEGIN SELECT RAISE(ABORT,'journal acceptances are append-only'); END;
CREATE TRIGGER identity_acceptances_no_delete BEFORE DELETE ON identity_journal_acceptances BEGIN SELECT RAISE(ABORT,'journal acceptances are append-only'); END;
CREATE TRIGGER identity_migrations_no_update BEFORE UPDATE ON identity_reference_migrations BEGIN SELECT RAISE(ABORT,'reference migrations are append-only'); END;
CREATE TRIGGER identity_migrations_no_delete BEFORE DELETE ON identity_reference_migrations BEGIN SELECT RAISE(ABORT,'reference migrations are append-only'); END;
CREATE TRIGGER identity_bindings_no_update BEFORE UPDATE ON identity_reference_bindings BEGIN SELECT RAISE(ABORT,'reference bindings are append-only'); END;
CREATE TRIGGER identity_bindings_no_delete BEFORE DELETE ON identity_reference_bindings BEGIN SELECT RAISE(ABORT,'reference bindings are append-only'); END;
CREATE TRIGGER identity_history_no_update BEFORE UPDATE ON identity_link_history BEGIN SELECT RAISE(ABORT,'identity history is append-only'); END;
CREATE TRIGGER identity_history_no_delete BEFORE DELETE ON identity_link_history BEGIN SELECT RAISE(ABORT,'identity history is append-only'); END;
CREATE TRIGGER identity_links_retain_deleted AFTER DELETE ON identity_links BEGIN
    INSERT INTO identity_link_history(subject_kind,subject_id,state,assertion_json,resolution_json,recorded_at)
    VALUES (
        OLD.subject_kind,OLD.subject_id,'superseded',
        json_object('link_id',OLD.link_id,'wsdc_id',OLD.wsdc_id,'method',OLD.method,'status',OLD.status,'confidence',OLD.confidence,'constraints_applied',OLD.constraints_applied,'asserted_at',OLD.asserted_at,'run_id',OLD.run_id,'linker_version',OLD.linker_version),
        COALESCE((SELECT json_object('ref_ids',ref_ids_json,'decision_ids',decision_ids_json,'policy_version',policy_version,'journal_digest',journal_digest,'journal_generation',journal_generation,'state',state,'reason',reason) FROM identity_link_resolutions WHERE subject_kind=OLD.subject_kind AND subject_id=OLD.subject_id),'{}'),
        strftime('%Y-%m-%dT%H:%M:%fZ','now')
    );
    UPDATE identity_link_resolutions SET state='superseded' WHERE subject_kind=OLD.subject_kind AND subject_id=OLD.subject_id;
END;
CREATE TRIGGER work_enqueued AFTER INSERT ON pending_work BEGIN
    INSERT INTO work_generations(stage,unit_kind,unit_id,generation)
    VALUES(NEW.stage,NEW.unit_kind,NEW.unit_id,1)
    ON CONFLICT(stage,unit_kind,unit_id) DO UPDATE SET generation=generation+1;
END;
CREATE TRIGGER work_invalidated AFTER UPDATE ON pending_work BEGIN
    UPDATE work_generations SET generation=generation+1
    WHERE stage=NEW.stage AND unit_kind=NEW.unit_kind AND unit_id=NEW.unit_id;
END;
CREATE INDEX work_attempts_unit ON work_attempts(stage,unit_kind,unit_id,attempt_id DESC);
CREATE INDEX work_attempts_running ON work_attempts(outcome,attempt_id);
CREATE INDEX work_attempts_run ON work_attempts(run_id);
CREATE UNIQUE INDEX operator_pauses_id ON operator_pauses(pause_id);
CREATE INDEX control_events_pause ON control_events(pause_id,event_id);
CREATE INDEX execution_admissions_run ON execution_admissions(run_id);
CREATE INDEX execution_admissions_work ON execution_admissions(work_attempt_id);
CREATE INDEX execution_admissions_state ON execution_admissions(state,admitted_at);
CREATE UNIQUE INDEX execution_one_publication ON execution_admissions((1))
    WHERE state='active' AND action_kind IN ('publish','publication');
CREATE INDEX findings_subject_id_control_idx ON findings(subject_id);
CREATE TRIGGER "publication_fence_meta_insert" BEFORE INSERT ON "meta" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_meta_update" BEFORE UPDATE ON "meta" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_meta_delete" BEFORE DELETE ON "meta" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_narrow_fence_meta_insert" BEFORE INSERT ON "meta" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind='publication_narrow') AND (NEW.key IN ('input_bundle_hash','identity_journal_digest') AND NOT EXISTS(SELECT 1 FROM "meta" k WHERE k."key"=NEW."key")) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_narrow_fence_meta_update" BEFORE UPDATE ON "meta" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind='publication_narrow') AND ((OLD.key IN ('input_bundle_hash','identity_journal_digest') OR NEW.key IN ('input_bundle_hash','identity_journal_digest')) AND NEW."value" IS NOT OLD."value") BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_narrow_fence_meta_delete" BEFORE DELETE ON "meta" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind='publication_narrow') AND (OLD.key IN ('input_bundle_hash','identity_journal_digest')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_runs_insert" BEFORE INSERT ON "runs" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_runs_update" BEFORE UPDATE ON "runs" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_runs_delete" BEFORE DELETE ON "runs" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_hosts_insert" BEFORE INSERT ON "hosts" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_hosts_update" BEFORE UPDATE ON "hosts" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_hosts_delete" BEFORE DELETE ON "hosts" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_host_budget_insert" BEFORE INSERT ON "host_budget" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_host_budget_update" BEFORE UPDATE ON "host_budget" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_host_budget_delete" BEFORE DELETE ON "host_budget" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_cursors_insert" BEFORE INSERT ON "cursors" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_cursors_update" BEFORE UPDATE ON "cursors" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_cursors_delete" BEFORE DELETE ON "cursors" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_watches_insert" BEFORE INSERT ON "watches" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_watches_update" BEFORE UPDATE ON "watches" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_watches_delete" BEFORE DELETE ON "watches" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_snapshots_insert" BEFORE INSERT ON "snapshots" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_snapshots_update" BEFORE UPDATE ON "snapshots" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_snapshots_delete" BEFORE DELETE ON "snapshots" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_observations_insert" BEFORE INSERT ON "observations" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_observations_update" BEFORE UPDATE ON "observations" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_observations_delete" BEFORE DELETE ON "observations" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_source_event_map_insert" BEFORE INSERT ON "source_event_map" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_source_event_map_update" BEFORE UPDATE ON "source_event_map" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_source_event_map_delete" BEFORE DELETE ON "source_event_map" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_revisions_insert" BEFORE INSERT ON "revisions" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_revisions_update" BEFORE UPDATE ON "revisions" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_revisions_delete" BEFORE DELETE ON "revisions" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_narrow_fence_revisions_insert" BEFORE INSERT ON "revisions" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind='publication_narrow') AND (NEW.name='identity_decisions' AND NOT EXISTS(SELECT 1 FROM "revisions" k WHERE k."name"=NEW."name")) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_narrow_fence_revisions_update" BEFORE UPDATE ON "revisions" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind='publication_narrow') AND ((OLD.name='identity_decisions' OR NEW.name='identity_decisions') AND NEW."value" IS NOT OLD."value") BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_narrow_fence_revisions_delete" BEFORE DELETE ON "revisions" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind='publication_narrow') AND (OLD.name='identity_decisions') BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_pending_work_insert" BEFORE INSERT ON "pending_work" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_pending_work_update" BEFORE UPDATE ON "pending_work" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_pending_work_delete" BEFORE DELETE ON "pending_work" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_accepted_inputs_insert" BEFORE INSERT ON "accepted_inputs" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_accepted_inputs_update" BEFORE UPDATE ON "accepted_inputs" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_accepted_inputs_delete" BEFORE DELETE ON "accepted_inputs" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_findings_insert" BEFORE INSERT ON "findings" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_findings_update" BEFORE UPDATE ON "findings" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_findings_delete" BEFORE DELETE ON "findings" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_source_events_insert" BEFORE INSERT ON "source_events" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_source_events_update" BEFORE UPDATE ON "source_events" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_source_events_delete" BEFORE DELETE ON "source_events" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_source_event_scope_rows_insert" BEFORE INSERT ON "source_event_scope_rows" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_source_event_scope_rows_update" BEFORE UPDATE ON "source_event_scope_rows" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_source_event_scope_rows_delete" BEFORE DELETE ON "source_event_scope_rows" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_backup_uploads_insert" BEFORE INSERT ON "backup_uploads" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_backup_uploads_update" BEFORE UPDATE ON "backup_uploads" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_backup_uploads_delete" BEFORE DELETE ON "backup_uploads" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_canonical_scope_rows_insert" BEFORE INSERT ON "canonical_scope_rows" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_canonical_scope_rows_update" BEFORE UPDATE ON "canonical_scope_rows" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_canonical_scope_rows_delete" BEFORE DELETE ON "canonical_scope_rows" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_contests_insert" BEFORE INSERT ON "contests" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_contests_update" BEFORE UPDATE ON "contests" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_contests_delete" BEFORE DELETE ON "contests" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_rounds_insert" BEFORE INSERT ON "rounds" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_rounds_update" BEFORE UPDATE ON "rounds" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_rounds_delete" BEFORE DELETE ON "rounds" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_entries_insert" BEFORE INSERT ON "entries" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_entries_update" BEFORE UPDATE ON "entries" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_entries_delete" BEFORE DELETE ON "entries" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_judges_insert" BEFORE INSERT ON "judges" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_judges_update" BEFORE UPDATE ON "judges" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_judges_delete" BEFORE DELETE ON "judges" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_heats_insert" BEFORE INSERT ON "heats" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_heats_update" BEFORE UPDATE ON "heats" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_heats_delete" BEFORE DELETE ON "heats" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_callback_marks_insert" BEFORE INSERT ON "callback_marks" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_callback_marks_update" BEFORE UPDATE ON "callback_marks" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_callback_marks_delete" BEFORE DELETE ON "callback_marks" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_callbacks_insert" BEFORE INSERT ON "callbacks" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_callbacks_update" BEFORE UPDATE ON "callbacks" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_callbacks_delete" BEFORE DELETE ON "callbacks" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_placements_insert" BEFORE INSERT ON "placements" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_placements_update" BEFORE UPDATE ON "placements" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_placements_delete" BEFORE DELETE ON "placements" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_final_marks_insert" BEFORE INSERT ON "final_marks" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_final_marks_update" BEFORE UPDATE ON "final_marks" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_final_marks_delete" BEFORE DELETE ON "final_marks" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_dancers_insert" BEFORE INSERT ON "dancers" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_dancers_update" BEFORE UPDATE ON "dancers" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_dancers_delete" BEFORE DELETE ON "dancers" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_registry_placements_insert" BEFORE INSERT ON "registry_placements" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_registry_placements_update" BEFORE UPDATE ON "registry_placements" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_registry_placements_delete" BEFORE DELETE ON "registry_placements" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_identity_links_insert" BEFORE INSERT ON "identity_links" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_identity_links_update" BEFORE UPDATE ON "identity_links" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_identity_links_delete" BEFORE DELETE ON "identity_links" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_registry_verifications_insert" BEFORE INSERT ON "registry_verifications" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_registry_verifications_update" BEFORE UPDATE ON "registry_verifications" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_registry_verifications_delete" BEFORE DELETE ON "registry_verifications" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_events_insert" BEFORE INSERT ON "events" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_events_update" BEFORE UPDATE ON "events" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_events_delete" BEFORE DELETE ON "events" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_archive_captures_insert" BEFORE INSERT ON "archive_captures" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_archive_captures_update" BEFORE UPDATE ON "archive_captures" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_archive_captures_delete" BEFORE DELETE ON "archive_captures" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_archive_queries_insert" BEFORE INSERT ON "archive_queries" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_archive_queries_update" BEFORE UPDATE ON "archive_queries" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_archive_queries_delete" BEFORE DELETE ON "archive_queries" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_series_insert" BEFORE INSERT ON "series" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_series_update" BEFORE UPDATE ON "series" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_series_delete" BEFORE DELETE ON "series" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_coverage_insert" BEFORE INSERT ON "coverage" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_coverage_update" BEFORE UPDATE ON "coverage" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_coverage_delete" BEFORE DELETE ON "coverage" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_history_acceptance_insert" BEFORE INSERT ON "history_acceptance" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_history_acceptance_update" BEFORE UPDATE ON "history_acceptance" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_history_acceptance_delete" BEFORE DELETE ON "history_acceptance" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_link_candidates_insert" BEFORE INSERT ON "link_candidates" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_link_candidates_update" BEFORE UPDATE ON "link_candidates" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_link_candidates_delete" BEFORE DELETE ON "link_candidates" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_finding_support_insert" BEFORE INSERT ON "finding_support" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_finding_support_update" BEFORE UPDATE ON "finding_support" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_finding_support_delete" BEFORE DELETE ON "finding_support" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_requirement_scan_insert" BEFORE INSERT ON "requirement_scan" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_requirement_scan_update" BEFORE UPDATE ON "requirement_scan" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_requirement_scan_delete" BEFORE DELETE ON "requirement_scan" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_admission_policies_insert" BEFORE INSERT ON "admission_policies" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_admission_policies_update" BEFORE UPDATE ON "admission_policies" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_admission_policies_delete" BEFORE DELETE ON "admission_policies" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_narrow_fence_admission_policies_insert" BEFORE INSERT ON "admission_policies" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind='publication_narrow') AND (NOT EXISTS(SELECT 1 FROM "admission_policies" k WHERE k."page_kind"=NEW."page_kind")) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_narrow_fence_admission_policies_update" BEFORE UPDATE ON "admission_policies" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind='publication_narrow') AND (1) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_narrow_fence_admission_policies_delete" BEFORE DELETE ON "admission_policies" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind='publication_narrow') AND (1) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_admission_reviews_insert" BEFORE INSERT ON "admission_reviews" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_admission_reviews_update" BEFORE UPDATE ON "admission_reviews" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_admission_reviews_delete" BEFORE DELETE ON "admission_reviews" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_source_units_insert" BEFORE INSERT ON "source_units" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_source_units_update" BEFORE UPDATE ON "source_units" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_source_units_delete" BEFORE DELETE ON "source_units" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_source_generations_insert" BEFORE INSERT ON "source_generations" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_source_generations_delete" BEFORE DELETE ON "source_generations" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_narrow_fence_source_generations_insert" BEFORE INSERT ON "source_generations" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind='publication_narrow') AND (NEW.state='revoked' AND NOT EXISTS(SELECT 1 FROM "source_generations" k WHERE k."generation_id"=NEW."generation_id")) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_narrow_fence_source_generations_delete" BEFORE DELETE ON "source_generations" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind='publication_narrow') AND (OLD.state='revoked') BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_admission_decisions_insert" BEFORE INSERT ON "admission_decisions" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_admission_decisions_update" BEFORE UPDATE ON "admission_decisions" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_admission_decisions_delete" BEFORE DELETE ON "admission_decisions" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_identity_decisions_insert" BEFORE INSERT ON "identity_decisions" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_identity_decisions_update" BEFORE UPDATE ON "identity_decisions" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_identity_decisions_delete" BEFORE DELETE ON "identity_decisions" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_narrow_fence_identity_decisions_insert" BEFORE INSERT ON "identity_decisions" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind='publication_narrow') AND (NOT EXISTS(SELECT 1 FROM "identity_decisions" k WHERE k."decision_id"=NEW."decision_id")) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_narrow_fence_identity_decisions_update" BEFORE UPDATE ON "identity_decisions" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind='publication_narrow') AND (1) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_narrow_fence_identity_decisions_delete" BEFORE DELETE ON "identity_decisions" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind='publication_narrow') AND (1) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_identity_journal_acceptances_insert" BEFORE INSERT ON "identity_journal_acceptances" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_identity_journal_acceptances_update" BEFORE UPDATE ON "identity_journal_acceptances" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_identity_journal_acceptances_delete" BEFORE DELETE ON "identity_journal_acceptances" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_narrow_fence_identity_journal_acceptances_insert" BEFORE INSERT ON "identity_journal_acceptances" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind='publication_narrow') AND (NOT EXISTS(SELECT 1 FROM "identity_journal_acceptances" k WHERE k."digest"=NEW."digest")) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_narrow_fence_identity_journal_acceptances_update" BEFORE UPDATE ON "identity_journal_acceptances" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind='publication_narrow') AND (1) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_narrow_fence_identity_journal_acceptances_delete" BEFORE DELETE ON "identity_journal_acceptances" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind='publication_narrow') AND (1) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_identity_source_refs_insert" BEFORE INSERT ON "identity_source_refs" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_identity_source_refs_update" BEFORE UPDATE ON "identity_source_refs" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_identity_source_refs_delete" BEFORE DELETE ON "identity_source_refs" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_identity_reference_bindings_insert" BEFORE INSERT ON "identity_reference_bindings" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_identity_reference_bindings_update" BEFORE UPDATE ON "identity_reference_bindings" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_identity_reference_bindings_delete" BEFORE DELETE ON "identity_reference_bindings" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_identity_reference_migrations_insert" BEFORE INSERT ON "identity_reference_migrations" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_identity_reference_migrations_update" BEFORE UPDATE ON "identity_reference_migrations" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_identity_reference_migrations_delete" BEFORE DELETE ON "identity_reference_migrations" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_identity_link_resolutions_insert" BEFORE INSERT ON "identity_link_resolutions" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_identity_link_resolutions_update" BEFORE UPDATE ON "identity_link_resolutions" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_identity_link_resolutions_delete" BEFORE DELETE ON "identity_link_resolutions" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_identity_link_history_insert" BEFORE INSERT ON "identity_link_history" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_identity_link_history_update" BEFORE UPDATE ON "identity_link_history" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_identity_link_history_delete" BEFORE DELETE ON "identity_link_history" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_work_generations_insert" BEFORE INSERT ON "work_generations" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_work_generations_update" BEFORE UPDATE ON "work_generations" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_work_generations_delete" BEFORE DELETE ON "work_generations" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_work_attempts_insert" BEFORE INSERT ON "work_attempts" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_work_attempts_update" BEFORE UPDATE ON "work_attempts" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_work_attempts_delete" BEFORE DELETE ON "work_attempts" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE INDEX scheduler_requests_class ON scheduler_requests(day,host,category);
CREATE INDEX scheduler_requests_run ON scheduler_requests(run_id,pressure_reserved);
CREATE INDEX scheduler_requests_service ON scheduler_requests(host,category,issued_at);
CREATE INDEX scheduler_parent_links_child ON scheduler_parent_links(child_watch_id);
CREATE INDEX scheduler_parent_links_snapshot ON scheduler_parent_links(first_snapshot_id);
CREATE TRIGGER "publication_fence_scheduler_requests_insert" BEFORE INSERT ON "scheduler_requests" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_scheduler_requests_update" BEFORE UPDATE ON "scheduler_requests" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_scheduler_requests_delete" BEFORE DELETE ON "scheduler_requests" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_scheduler_offline_service_insert" BEFORE INSERT ON "scheduler_offline_service" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_scheduler_offline_service_update" BEFORE UPDATE ON "scheduler_offline_service" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_scheduler_offline_service_delete" BEFORE DELETE ON "scheduler_offline_service" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_scheduler_watch_state_insert" BEFORE INSERT ON "scheduler_watch_state" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_scheduler_watch_state_update" BEFORE UPDATE ON "scheduler_watch_state" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_scheduler_watch_state_delete" BEFORE DELETE ON "scheduler_watch_state" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_scheduler_parent_links_insert" BEFORE INSERT ON "scheduler_parent_links" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_scheduler_parent_links_update" BEFORE UPDATE ON "scheduler_parent_links" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_scheduler_parent_links_delete" BEFORE DELETE ON "scheduler_parent_links" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE INDEX derivation_scopes_materialized ON derivation_scopes(materialized_generation_id);
CREATE INDEX derivation_generations_scope ON derivation_generations(stage,unit_kind,unit_id,created_at);
CREATE INDEX derivation_generations_dependencies ON derivation_generations(dependency_set_id);
CREATE INDEX derivation_generations_previous ON derivation_generations(previous_generation_id);
CREATE INDEX derivation_generations_run ON derivation_generations(run_id);
CREATE TRIGGER derivation_generation_no_delete BEFORE DELETE ON derivation_generations
BEGIN SELECT RAISE(ABORT,'derivation generations are immutable'); END;
CREATE TRIGGER derivation_dependency_no_update BEFORE UPDATE ON derivation_dependency_sets
BEGIN SELECT RAISE(ABORT,'derivation dependency manifests are immutable'); END;
CREATE TRIGGER derivation_version_no_delete BEFORE DELETE ON derivation_input_versions
BEGIN SELECT RAISE(ABORT,'derivation input versions cannot be deleted'); END;
CREATE TRIGGER derivation_version_monotonic BEFORE UPDATE ON derivation_input_versions
WHEN NEW.namespace IS NOT OLD.namespace OR NEW.input_key IS NOT OLD.input_key OR NEW.version <= OLD.version
BEGIN SELECT RAISE(ABORT,'derivation input versions must advance'); END;
CREATE TRIGGER derivation_observations_insert AFTER INSERT ON observations
BEGIN INSERT INTO derivation_input_versions VALUES ('raw',json_array(NEW.scope_kind,NEW.scope_id),1) ON CONFLICT(namespace,input_key) DO UPDATE SET version=version+1;INSERT OR IGNORE INTO derivation_scopes(stage,unit_kind,unit_id,registered_at) SELECT 'project',NEW.scope_kind,NEW.scope_id,strftime('%Y-%m-%dT%H:%M:%f+00:00','now') WHERE NEW.scope_kind IN ('calendar','source_index','dancer','event','source_event','inventory','map','history') AND NOT EXISTS (SELECT 1 FROM derivation_scopes WHERE stage='project' AND unit_kind=NEW.scope_kind AND unit_id=NEW.scope_id); END;
CREATE TRIGGER derivation_source_units_insert AFTER INSERT ON source_units
BEGIN INSERT INTO derivation_input_versions VALUES ('watch',NEW.watch_id,1) ON CONFLICT(namespace,input_key) DO UPDATE SET version=version+1; END;
CREATE TRIGGER derivation_snapshots_insert AFTER INSERT ON snapshots
BEGIN INSERT INTO derivation_input_versions VALUES ('watch',NEW.watch_id,1) ON CONFLICT(namespace,input_key) DO UPDATE SET version=version+1; END;
CREATE TRIGGER derivation_source_event_map_insert AFTER INSERT ON source_event_map
BEGIN INSERT INTO derivation_input_versions VALUES ('mapping',NEW.event_id,1) ON CONFLICT(namespace,input_key) DO UPDATE SET version=version+1;INSERT INTO derivation_input_versions VALUES ('source_mapping',NEW.source_ref,1) ON CONFLICT(namespace,input_key) DO UPDATE SET version=version+1;INSERT OR IGNORE INTO derivation_scopes(stage,unit_kind,unit_id,registered_at) SELECT 'project','event',NEW.event_id,strftime('%Y-%m-%dT%H:%M:%f+00:00','now') WHERE 'event' IN ('calendar','source_index','dancer','event','source_event','inventory','map','history') AND NOT EXISTS (SELECT 1 FROM derivation_scopes WHERE stage='project' AND unit_kind='event' AND unit_id=NEW.event_id);INSERT OR IGNORE INTO derivation_scopes(stage,unit_kind,unit_id,registered_at) SELECT 'project','source_event',NEW.source_ref,strftime('%Y-%m-%dT%H:%M:%f+00:00','now') WHERE 'source_event' IN ('calendar','source_index','dancer','event','source_event','inventory','map','history') AND NOT EXISTS (SELECT 1 FROM derivation_scopes WHERE stage='project' AND unit_kind='source_event' AND unit_id=NEW.source_ref); END;
CREATE TRIGGER derivation_findings_insert AFTER INSERT ON findings WHEN (NEW.kind='missing_identity' AND NEW.subject_kind='round')
BEGIN INSERT INTO derivation_input_versions VALUES ('missing_identity',NEW.subject_id,1) ON CONFLICT(namespace,input_key) DO UPDATE SET version=version+1; END;
CREATE TRIGGER derivation_derivation_scopes_insert AFTER INSERT ON derivation_scopes
BEGIN INSERT INTO derivation_input_versions VALUES ('selected',json_array(NEW.stage,NEW.unit_kind),1) ON CONFLICT(namespace,input_key) DO UPDATE SET version=version+1; END;
CREATE TRIGGER derivation_observations_update AFTER UPDATE ON observations
BEGIN INSERT INTO derivation_input_versions VALUES ('raw',json_array(OLD.scope_kind,OLD.scope_id),1) ON CONFLICT(namespace,input_key) DO UPDATE SET version=version+1;INSERT OR IGNORE INTO derivation_scopes(stage,unit_kind,unit_id,registered_at) SELECT 'project',OLD.scope_kind,OLD.scope_id,strftime('%Y-%m-%dT%H:%M:%f+00:00','now') WHERE OLD.scope_kind IN ('calendar','source_index','dancer','event','source_event','inventory','map','history') AND NOT EXISTS (SELECT 1 FROM derivation_scopes WHERE stage='project' AND unit_kind=OLD.scope_kind AND unit_id=OLD.scope_id); INSERT INTO derivation_input_versions VALUES ('raw',json_array(NEW.scope_kind,NEW.scope_id),1) ON CONFLICT(namespace,input_key) DO UPDATE SET version=version+1;INSERT OR IGNORE INTO derivation_scopes(stage,unit_kind,unit_id,registered_at) SELECT 'project',NEW.scope_kind,NEW.scope_id,strftime('%Y-%m-%dT%H:%M:%f+00:00','now') WHERE NEW.scope_kind IN ('calendar','source_index','dancer','event','source_event','inventory','map','history') AND NOT EXISTS (SELECT 1 FROM derivation_scopes WHERE stage='project' AND unit_kind=NEW.scope_kind AND unit_id=NEW.scope_id); END;
CREATE TRIGGER derivation_snapshots_update AFTER UPDATE ON snapshots WHEN OLD.watch_id IS NOT NEW.watch_id OR OLD.body_sha256 IS NOT NEW.body_sha256 OR OLD.observed_at IS NOT NEW.observed_at OR OLD.fetched_at IS NOT NEW.fetched_at OR OLD.via IS NOT NEW.via
BEGIN INSERT INTO derivation_input_versions VALUES ('watch',OLD.watch_id,1) ON CONFLICT(namespace,input_key) DO UPDATE SET version=version+1; INSERT INTO derivation_input_versions VALUES ('watch',NEW.watch_id,1) ON CONFLICT(namespace,input_key) DO UPDATE SET version=version+1; END;
CREATE TRIGGER derivation_source_event_map_update AFTER UPDATE ON source_event_map
BEGIN INSERT INTO derivation_input_versions VALUES ('mapping',OLD.event_id,1) ON CONFLICT(namespace,input_key) DO UPDATE SET version=version+1;INSERT INTO derivation_input_versions VALUES ('source_mapping',OLD.source_ref,1) ON CONFLICT(namespace,input_key) DO UPDATE SET version=version+1;INSERT OR IGNORE INTO derivation_scopes(stage,unit_kind,unit_id,registered_at) SELECT 'project','event',OLD.event_id,strftime('%Y-%m-%dT%H:%M:%f+00:00','now') WHERE 'event' IN ('calendar','source_index','dancer','event','source_event','inventory','map','history') AND NOT EXISTS (SELECT 1 FROM derivation_scopes WHERE stage='project' AND unit_kind='event' AND unit_id=OLD.event_id);INSERT OR IGNORE INTO derivation_scopes(stage,unit_kind,unit_id,registered_at) SELECT 'project','source_event',OLD.source_ref,strftime('%Y-%m-%dT%H:%M:%f+00:00','now') WHERE 'source_event' IN ('calendar','source_index','dancer','event','source_event','inventory','map','history') AND NOT EXISTS (SELECT 1 FROM derivation_scopes WHERE stage='project' AND unit_kind='source_event' AND unit_id=OLD.source_ref); INSERT INTO derivation_input_versions VALUES ('mapping',NEW.event_id,1) ON CONFLICT(namespace,input_key) DO UPDATE SET version=version+1;INSERT INTO derivation_input_versions VALUES ('source_mapping',NEW.source_ref,1) ON CONFLICT(namespace,input_key) DO UPDATE SET version=version+1;INSERT OR IGNORE INTO derivation_scopes(stage,unit_kind,unit_id,registered_at) SELECT 'project','event',NEW.event_id,strftime('%Y-%m-%dT%H:%M:%f+00:00','now') WHERE 'event' IN ('calendar','source_index','dancer','event','source_event','inventory','map','history') AND NOT EXISTS (SELECT 1 FROM derivation_scopes WHERE stage='project' AND unit_kind='event' AND unit_id=NEW.event_id);INSERT OR IGNORE INTO derivation_scopes(stage,unit_kind,unit_id,registered_at) SELECT 'project','source_event',NEW.source_ref,strftime('%Y-%m-%dT%H:%M:%f+00:00','now') WHERE 'source_event' IN ('calendar','source_index','dancer','event','source_event','inventory','map','history') AND NOT EXISTS (SELECT 1 FROM derivation_scopes WHERE stage='project' AND unit_kind='source_event' AND unit_id=NEW.source_ref); END;
CREATE TRIGGER derivation_findings_update AFTER UPDATE ON findings WHEN (OLD.kind='missing_identity' AND OLD.subject_kind='round') OR (NEW.kind='missing_identity' AND NEW.subject_kind='round')
BEGIN INSERT INTO derivation_input_versions VALUES ('missing_identity',OLD.subject_id,1) ON CONFLICT(namespace,input_key) DO UPDATE SET version=version+1; INSERT INTO derivation_input_versions VALUES ('missing_identity',NEW.subject_id,1) ON CONFLICT(namespace,input_key) DO UPDATE SET version=version+1; END;
CREATE TRIGGER derivation_derivation_scopes_update AFTER UPDATE ON derivation_scopes WHEN OLD.materialized_generation_id IS NOT NEW.materialized_generation_id
BEGIN INSERT INTO derivation_input_versions VALUES ('selected',json_array(OLD.stage,OLD.unit_kind),1) ON CONFLICT(namespace,input_key) DO UPDATE SET version=version+1; INSERT INTO derivation_input_versions VALUES ('selected',json_array(NEW.stage,NEW.unit_kind),1) ON CONFLICT(namespace,input_key) DO UPDATE SET version=version+1; END;
CREATE TRIGGER derivation_observations_delete AFTER DELETE ON observations
BEGIN INSERT INTO derivation_input_versions VALUES ('raw',json_array(OLD.scope_kind,OLD.scope_id),1) ON CONFLICT(namespace,input_key) DO UPDATE SET version=version+1;INSERT OR IGNORE INTO derivation_scopes(stage,unit_kind,unit_id,registered_at) SELECT 'project',OLD.scope_kind,OLD.scope_id,strftime('%Y-%m-%dT%H:%M:%f+00:00','now') WHERE OLD.scope_kind IN ('calendar','source_index','dancer','event','source_event','inventory','map','history') AND NOT EXISTS (SELECT 1 FROM derivation_scopes WHERE stage='project' AND unit_kind=OLD.scope_kind AND unit_id=OLD.scope_id); END;
CREATE TRIGGER derivation_source_units_delete AFTER DELETE ON source_units
BEGIN INSERT INTO derivation_input_versions VALUES ('watch',OLD.watch_id,1) ON CONFLICT(namespace,input_key) DO UPDATE SET version=version+1; END;
CREATE TRIGGER derivation_snapshots_delete AFTER DELETE ON snapshots
BEGIN INSERT INTO derivation_input_versions VALUES ('watch',OLD.watch_id,1) ON CONFLICT(namespace,input_key) DO UPDATE SET version=version+1; END;
CREATE TRIGGER derivation_source_event_map_delete AFTER DELETE ON source_event_map
BEGIN INSERT INTO derivation_input_versions VALUES ('mapping',OLD.event_id,1) ON CONFLICT(namespace,input_key) DO UPDATE SET version=version+1;INSERT INTO derivation_input_versions VALUES ('source_mapping',OLD.source_ref,1) ON CONFLICT(namespace,input_key) DO UPDATE SET version=version+1;INSERT OR IGNORE INTO derivation_scopes(stage,unit_kind,unit_id,registered_at) SELECT 'project','event',OLD.event_id,strftime('%Y-%m-%dT%H:%M:%f+00:00','now') WHERE 'event' IN ('calendar','source_index','dancer','event','source_event','inventory','map','history') AND NOT EXISTS (SELECT 1 FROM derivation_scopes WHERE stage='project' AND unit_kind='event' AND unit_id=OLD.event_id);INSERT OR IGNORE INTO derivation_scopes(stage,unit_kind,unit_id,registered_at) SELECT 'project','source_event',OLD.source_ref,strftime('%Y-%m-%dT%H:%M:%f+00:00','now') WHERE 'source_event' IN ('calendar','source_index','dancer','event','source_event','inventory','map','history') AND NOT EXISTS (SELECT 1 FROM derivation_scopes WHERE stage='project' AND unit_kind='source_event' AND unit_id=OLD.source_ref); END;
CREATE TRIGGER derivation_findings_delete AFTER DELETE ON findings WHEN (OLD.kind='missing_identity' AND OLD.subject_kind='round')
BEGIN INSERT INTO derivation_input_versions VALUES ('missing_identity',OLD.subject_id,1) ON CONFLICT(namespace,input_key) DO UPDATE SET version=version+1; END;
CREATE TRIGGER derivation_derivation_scopes_delete AFTER DELETE ON derivation_scopes
BEGIN INSERT INTO derivation_input_versions VALUES ('selected',json_array(OLD.stage,OLD.unit_kind),1) ON CONFLICT(namespace,input_key) DO UPDATE SET version=version+1; END;
CREATE TRIGGER derivation_events_insert AFTER INSERT ON events
BEGIN INSERT INTO derivation_input_versions VALUES ('canonical',coalesce(NEW.event_id,''),1) ON CONFLICT(namespace,input_key) DO UPDATE SET version=version+1;INSERT OR IGNORE INTO derivation_scopes(stage,unit_kind,unit_id,registered_at) SELECT 'project','event',NEW.event_id,strftime('%Y-%m-%dT%H:%M:%f+00:00','now') WHERE 'event' IN ('calendar','source_index','dancer','event','source_event','inventory','map','history') AND NOT EXISTS (SELECT 1 FROM derivation_scopes WHERE stage='project' AND unit_kind='event' AND unit_id=NEW.event_id); END;
CREATE TRIGGER derivation_events_update AFTER UPDATE ON events WHEN OLD.event_id IS NOT NEW.event_id OR OLD.series_id IS NOT NEW.series_id OR OLD.name IS NOT NEW.name OR OLD.year IS NOT NEW.year OR OLD.start_date IS NOT NEW.start_date OR OLD.end_date IS NOT NEW.end_date OR OLD.city IS NOT NEW.city OR OLD.region IS NOT NEW.region OR OLD.country IS NOT NEW.country OR OLD.website IS NOT NEW.website OR OLD.wsdc_status IS NOT NEW.wsdc_status OR OLD.sources IS NOT NEW.sources OR OLD.live_window_start IS NOT NEW.live_window_start OR OLD.live_window_end IS NOT NEW.live_window_end OR OLD.source IS NOT NEW.source OR OLD.snapshot_id IS NOT NEW.snapshot_id OR OLD.parser_version IS NOT NEW.parser_version OR OLD.event_month IS NOT NEW.event_month OR OLD.date_precision IS NOT NEW.date_precision OR OLD.held IS NOT NEW.held OR OLD.coverage_tier IS NOT NEW.coverage_tier OR OLD.history_source IS NOT NEW.history_source
BEGIN INSERT INTO derivation_input_versions VALUES ('canonical',coalesce(OLD.event_id,''),1) ON CONFLICT(namespace,input_key) DO UPDATE SET version=version+1;INSERT OR IGNORE INTO derivation_scopes(stage,unit_kind,unit_id,registered_at) SELECT 'project','event',OLD.event_id,strftime('%Y-%m-%dT%H:%M:%f+00:00','now') WHERE 'event' IN ('calendar','source_index','dancer','event','source_event','inventory','map','history') AND NOT EXISTS (SELECT 1 FROM derivation_scopes WHERE stage='project' AND unit_kind='event' AND unit_id=OLD.event_id);INSERT INTO derivation_input_versions VALUES ('canonical',coalesce(NEW.event_id,''),1) ON CONFLICT(namespace,input_key) DO UPDATE SET version=version+1;INSERT OR IGNORE INTO derivation_scopes(stage,unit_kind,unit_id,registered_at) SELECT 'project','event',NEW.event_id,strftime('%Y-%m-%dT%H:%M:%f+00:00','now') WHERE 'event' IN ('calendar','source_index','dancer','event','source_event','inventory','map','history') AND NOT EXISTS (SELECT 1 FROM derivation_scopes WHERE stage='project' AND unit_kind='event' AND unit_id=NEW.event_id); END;
CREATE TRIGGER derivation_events_delete AFTER DELETE ON events
BEGIN INSERT INTO derivation_input_versions VALUES ('canonical',coalesce(OLD.event_id,''),1) ON CONFLICT(namespace,input_key) DO UPDATE SET version=version+1;INSERT OR IGNORE INTO derivation_scopes(stage,unit_kind,unit_id,registered_at) SELECT 'project','event',OLD.event_id,strftime('%Y-%m-%dT%H:%M:%f+00:00','now') WHERE 'event' IN ('calendar','source_index','dancer','event','source_event','inventory','map','history') AND NOT EXISTS (SELECT 1 FROM derivation_scopes WHERE stage='project' AND unit_kind='event' AND unit_id=OLD.event_id); END;
CREATE TRIGGER derivation_entries_insert AFTER INSERT ON entries
BEGIN INSERT INTO derivation_input_versions VALUES ('canonical',coalesce(NEW.event_id,''),1) ON CONFLICT(namespace,input_key) DO UPDATE SET version=version+1; END;
CREATE TRIGGER derivation_entries_update AFTER UPDATE ON entries WHEN OLD.entry_id IS NOT NEW.entry_id OR OLD.contest_id IS NOT NEW.contest_id OR OLD.event_id IS NOT NEW.event_id OR OLD.role IS NOT NEW.role OR OLD.bib IS NOT NEW.bib OR OLD.name_raw IS NOT NEW.name_raw OR OLD.name_norm IS NOT NEW.name_norm OR OLD.partner_name_raw IS NOT NEW.partner_name_raw OR OLD.city_raw IS NOT NEW.city_raw OR OLD.country_raw IS NOT NEW.country_raw OR OLD.partner_entry_id IS NOT NEW.partner_entry_id OR OLD.rounds_danced IS NOT NEW.rounds_danced OR OLD.best_round IS NOT NEW.best_round OR OLD.source IS NOT NEW.source OR OLD.snapshot_id IS NOT NEW.snapshot_id OR OLD.parser_version IS NOT NEW.parser_version
BEGIN INSERT INTO derivation_input_versions VALUES ('canonical',coalesce(OLD.event_id,''),1) ON CONFLICT(namespace,input_key) DO UPDATE SET version=version+1;INSERT INTO derivation_input_versions VALUES ('canonical',coalesce(NEW.event_id,''),1) ON CONFLICT(namespace,input_key) DO UPDATE SET version=version+1; END;
CREATE TRIGGER derivation_entries_delete AFTER DELETE ON entries
BEGIN INSERT INTO derivation_input_versions VALUES ('canonical',coalesce(OLD.event_id,''),1) ON CONFLICT(namespace,input_key) DO UPDATE SET version=version+1; END;
CREATE TRIGGER derivation_judges_insert AFTER INSERT ON judges
BEGIN INSERT INTO derivation_input_versions VALUES ('canonical',coalesce(NEW.event_id,''),1) ON CONFLICT(namespace,input_key) DO UPDATE SET version=version+1; END;
CREATE TRIGGER derivation_judges_update AFTER UPDATE ON judges WHEN OLD.judge_id IS NOT NEW.judge_id OR OLD.event_id IS NOT NEW.event_id OR OLD.name_raw IS NOT NEW.name_raw OR OLD.initials IS NOT NEW.initials OR OLD.anonymous IS NOT NEW.anonymous OR OLD.source IS NOT NEW.source OR OLD.snapshot_id IS NOT NEW.snapshot_id OR OLD.parser_version IS NOT NEW.parser_version
BEGIN INSERT INTO derivation_input_versions VALUES ('canonical',coalesce(OLD.event_id,''),1) ON CONFLICT(namespace,input_key) DO UPDATE SET version=version+1;INSERT INTO derivation_input_versions VALUES ('canonical',coalesce(NEW.event_id,''),1) ON CONFLICT(namespace,input_key) DO UPDATE SET version=version+1; END;
CREATE TRIGGER derivation_judges_delete AFTER DELETE ON judges
BEGIN INSERT INTO derivation_input_versions VALUES ('canonical',coalesce(OLD.event_id,''),1) ON CONFLICT(namespace,input_key) DO UPDATE SET version=version+1; END;
CREATE TRIGGER derivation_contests_insert AFTER INSERT ON contests
BEGIN INSERT INTO derivation_input_versions VALUES ('canonical',coalesce(NEW.event_id,''),1) ON CONFLICT(namespace,input_key) DO UPDATE SET version=version+1; END;
CREATE TRIGGER derivation_contests_update AFTER UPDATE ON contests WHEN OLD.contest_id IS NOT NEW.contest_id OR OLD.event_id IS NOT NEW.event_id OR OLD.name_raw IS NOT NEW.name_raw OR OLD.division IS NOT NEW.division OR OLD.age_division IS NOT NEW.age_division OR OLD.contest_type IS NOT NEW.contest_type OR OLD.partner_mode IS NOT NEW.partner_mode OR OLD.dance_style IS NOT NEW.dance_style OR OLD.wsdc_points_eligible IS NOT NEW.wsdc_points_eligible OR OLD.combined_from IS NOT NEW.combined_from OR OLD.parse_status IS NOT NEW.parse_status OR OLD.source_contest_ref IS NOT NEW.source_contest_ref OR OLD.source IS NOT NEW.source OR OLD.snapshot_id IS NOT NEW.snapshot_id OR OLD.parser_version IS NOT NEW.parser_version
BEGIN INSERT INTO derivation_input_versions VALUES ('canonical',coalesce(OLD.event_id,''),1) ON CONFLICT(namespace,input_key) DO UPDATE SET version=version+1;INSERT INTO derivation_input_versions VALUES ('canonical',coalesce(NEW.event_id,''),1) ON CONFLICT(namespace,input_key) DO UPDATE SET version=version+1; END;
CREATE TRIGGER derivation_contests_delete AFTER DELETE ON contests
BEGIN INSERT INTO derivation_input_versions VALUES ('canonical',coalesce(OLD.event_id,''),1) ON CONFLICT(namespace,input_key) DO UPDATE SET version=version+1; END;
CREATE TRIGGER derivation_placements_insert AFTER INSERT ON placements
BEGIN INSERT INTO derivation_input_versions VALUES ('canonical',coalesce(NEW.event_id,''),1) ON CONFLICT(namespace,input_key) DO UPDATE SET version=version+1; END;
CREATE TRIGGER derivation_placements_update AFTER UPDATE ON placements WHEN OLD.placement_id IS NOT NEW.placement_id OR OLD.round_id IS NOT NEW.round_id OR OLD.contest_id IS NOT NEW.contest_id OR OLD.event_id IS NOT NEW.event_id OR OLD.place IS NOT NEW.place OR OLD.leader_entry_id IS NOT NEW.leader_entry_id OR OLD.follower_entry_id IS NOT NEW.follower_entry_id OR OLD.couple_entry_id IS NOT NEW.couple_entry_id OR OLD.marks_sorted IS NOT NEW.marks_sorted OR OLD.tally IS NOT NEW.tally OR OLD.source IS NOT NEW.source OR OLD.snapshot_id IS NOT NEW.snapshot_id OR OLD.parser_version IS NOT NEW.parser_version
BEGIN INSERT INTO derivation_input_versions VALUES ('canonical',coalesce(OLD.event_id,''),1) ON CONFLICT(namespace,input_key) DO UPDATE SET version=version+1;INSERT INTO derivation_input_versions VALUES ('canonical',coalesce(NEW.event_id,''),1) ON CONFLICT(namespace,input_key) DO UPDATE SET version=version+1; END;
CREATE TRIGGER derivation_placements_delete AFTER DELETE ON placements
BEGIN INSERT INTO derivation_input_versions VALUES ('canonical',coalesce(OLD.event_id,''),1) ON CONFLICT(namespace,input_key) DO UPDATE SET version=version+1; END;
CREATE TRIGGER derivation_rounds_insert AFTER INSERT ON rounds
BEGIN INSERT INTO derivation_input_versions VALUES ('canonical',coalesce((SELECT event_id FROM contests WHERE contest_id=NEW.contest_id),''),1) ON CONFLICT(namespace,input_key) DO UPDATE SET version=version+1; END;
CREATE TRIGGER derivation_rounds_update AFTER UPDATE ON rounds WHEN OLD.round_id IS NOT NEW.round_id OR OLD.contest_id IS NOT NEW.contest_id OR OLD.round_type IS NOT NEW.round_type OR OLD.round_index IS NOT NEW.round_index OR OLD.name_raw IS NOT NEW.name_raw OR OLD.scoring_method IS NOT NEW.scoring_method OR OLD.callback_legend IS NOT NEW.callback_legend OR OLD.judge_count IS NOT NEW.judge_count OR OLD.chief_judge_id IS NOT NEW.chief_judge_id OR OLD.entry_count IS NOT NEW.entry_count OR OLD.promoted_count IS NOT NEW.promoted_count OR OLD.source_round_ref IS NOT NEW.source_round_ref OR OLD.score_sheet_url IS NOT NEW.score_sheet_url OR OLD.source IS NOT NEW.source OR OLD.snapshot_id IS NOT NEW.snapshot_id OR OLD.parser_version IS NOT NEW.parser_version
BEGIN INSERT INTO derivation_input_versions VALUES ('canonical',coalesce((SELECT event_id FROM contests WHERE contest_id=OLD.contest_id),''),1) ON CONFLICT(namespace,input_key) DO UPDATE SET version=version+1;INSERT INTO derivation_input_versions VALUES ('canonical',coalesce((SELECT event_id FROM contests WHERE contest_id=NEW.contest_id),''),1) ON CONFLICT(namespace,input_key) DO UPDATE SET version=version+1; END;
CREATE TRIGGER derivation_rounds_delete AFTER DELETE ON rounds
BEGIN INSERT INTO derivation_input_versions VALUES ('canonical',coalesce((SELECT event_id FROM contests WHERE contest_id=OLD.contest_id),''),1) ON CONFLICT(namespace,input_key) DO UPDATE SET version=version+1; END;
CREATE TRIGGER derivation_history_acceptance_insert AFTER INSERT ON history_acceptance
BEGIN INSERT INTO derivation_input_versions VALUES ('history_acceptance','all',1) ON CONFLICT(namespace,input_key) DO UPDATE SET version=version+1; END;
CREATE TRIGGER derivation_registry_placements_insert AFTER INSERT ON registry_placements
BEGIN INSERT INTO derivation_input_versions VALUES ('inventory_registry','all',1) ON CONFLICT(namespace,input_key) DO UPDATE SET version=version+1; END;
CREATE TRIGGER derivation_source_events_insert AFTER INSERT ON source_events
BEGIN INSERT INTO derivation_input_versions VALUES ('inventory_sources','all',1) ON CONFLICT(namespace,input_key) DO UPDATE SET version=version+1; END;
CREATE TRIGGER derivation_watches_insert AFTER INSERT ON watches
BEGIN INSERT INTO derivation_input_versions VALUES ('watch_sources','all',1) ON CONFLICT(namespace,input_key) DO UPDATE SET version=version+1; END;
CREATE TRIGGER derivation_source_selections_insert AFTER INSERT ON source_units
BEGIN INSERT INTO derivation_input_versions VALUES ('source_selections','all',1) ON CONFLICT(namespace,input_key) DO UPDATE SET version=version+1; END;
CREATE TRIGGER derivation_calendar_raw_insert AFTER INSERT ON observations WHEN NEW.scope_kind='calendar'
BEGIN INSERT INTO derivation_input_versions VALUES ('calendar_raw','all',1) ON CONFLICT(namespace,input_key) DO UPDATE SET version=version+1; END;
CREATE TRIGGER derivation_index_raw_insert AFTER INSERT ON observations WHEN NEW.scope_kind='source_index'
BEGIN INSERT INTO derivation_input_versions VALUES ('index_raw','all',1) ON CONFLICT(namespace,input_key) DO UPDATE SET version=version+1; END;
CREATE TRIGGER derivation_history_findings_insert AFTER INSERT ON findings WHEN NEW.owner_kind IN ('history_year','phase1_year')
BEGIN INSERT INTO derivation_input_versions VALUES ('history_findings','all',1) ON CONFLICT(namespace,input_key) DO UPDATE SET version=version+1; END;
CREATE TRIGGER derivation_snapshot_basis_insert AFTER INSERT ON snapshots
BEGIN INSERT INTO derivation_input_versions VALUES ('snapshot_basis','all',1) ON CONFLICT(namespace,input_key) DO UPDATE SET version=version+1; END;
CREATE TRIGGER derivation_history_acceptance_update AFTER UPDATE ON history_acceptance
BEGIN INSERT INTO derivation_input_versions VALUES ('history_acceptance','all',1) ON CONFLICT(namespace,input_key) DO UPDATE SET version=version+1; END;
CREATE TRIGGER derivation_registry_placements_update AFTER UPDATE ON registry_placements WHEN OLD.wsdc_id IS NOT NEW.wsdc_id OR OLD.role IS NOT NEW.role OR OLD.dance_style IS NOT NEW.dance_style OR OLD.division IS NOT NEW.division OR OLD.series_id IS NOT NEW.series_id OR OLD.series_name_raw IS NOT NEW.series_name_raw OR OLD.event_month IS NOT NEW.event_month OR OLD.result IS NOT NEW.result OR OLD.points IS NOT NEW.points OR OLD.source IS NOT NEW.source OR OLD.snapshot_id IS NOT NEW.snapshot_id OR OLD.parser_version IS NOT NEW.parser_version OR OLD.first_seen_at IS NOT NEW.first_seen_at OR OLD.last_seen_at IS NOT NEW.last_seen_at OR OLD.run_id IS NOT NEW.run_id
BEGIN INSERT INTO derivation_input_versions VALUES ('inventory_registry','all',1) ON CONFLICT(namespace,input_key) DO UPDATE SET version=version+1; END;
CREATE TRIGGER derivation_source_events_update AFTER UPDATE ON source_events
BEGIN INSERT INTO derivation_input_versions VALUES ('inventory_sources','all',1) ON CONFLICT(namespace,input_key) DO UPDATE SET version=version+1; END;
CREATE TRIGGER derivation_watches_update AFTER UPDATE ON watches WHEN OLD.source IS NOT NEW.source OR OLD.kind IS NOT NEW.kind OR OLD.parser IS NOT NEW.parser
BEGIN INSERT INTO derivation_input_versions VALUES ('watch_sources','all',1) ON CONFLICT(namespace,input_key) DO UPDATE SET version=version+1; END;
CREATE TRIGGER derivation_calendar_raw_update AFTER UPDATE ON observations WHEN OLD.scope_kind='calendar' OR NEW.scope_kind='calendar'
BEGIN INSERT INTO derivation_input_versions VALUES ('calendar_raw','all',1) ON CONFLICT(namespace,input_key) DO UPDATE SET version=version+1; END;
CREATE TRIGGER derivation_index_raw_update AFTER UPDATE ON observations WHEN OLD.scope_kind='source_index' OR NEW.scope_kind='source_index'
BEGIN INSERT INTO derivation_input_versions VALUES ('index_raw','all',1) ON CONFLICT(namespace,input_key) DO UPDATE SET version=version+1; END;
CREATE TRIGGER derivation_history_findings_update AFTER UPDATE ON findings WHEN OLD.owner_kind IN ('history_year','phase1_year') OR NEW.owner_kind IN ('history_year','phase1_year')
BEGIN INSERT INTO derivation_input_versions VALUES ('history_findings','all',1) ON CONFLICT(namespace,input_key) DO UPDATE SET version=version+1; END;
CREATE TRIGGER derivation_snapshot_basis_update AFTER UPDATE ON snapshots WHEN OLD.observed_at IS NOT NEW.observed_at OR OLD.fetched_at IS NOT NEW.fetched_at OR OLD.via IS NOT NEW.via
BEGIN INSERT INTO derivation_input_versions VALUES ('snapshot_basis','all',1) ON CONFLICT(namespace,input_key) DO UPDATE SET version=version+1; END;
CREATE TRIGGER derivation_history_acceptance_delete AFTER DELETE ON history_acceptance
BEGIN INSERT INTO derivation_input_versions VALUES ('history_acceptance','all',1) ON CONFLICT(namespace,input_key) DO UPDATE SET version=version+1; END;
CREATE TRIGGER derivation_registry_placements_delete AFTER DELETE ON registry_placements
BEGIN INSERT INTO derivation_input_versions VALUES ('inventory_registry','all',1) ON CONFLICT(namespace,input_key) DO UPDATE SET version=version+1; END;
CREATE TRIGGER derivation_source_events_delete AFTER DELETE ON source_events
BEGIN INSERT INTO derivation_input_versions VALUES ('inventory_sources','all',1) ON CONFLICT(namespace,input_key) DO UPDATE SET version=version+1; END;
CREATE TRIGGER derivation_watches_delete AFTER DELETE ON watches
BEGIN INSERT INTO derivation_input_versions VALUES ('watch_sources','all',1) ON CONFLICT(namespace,input_key) DO UPDATE SET version=version+1; END;
CREATE TRIGGER derivation_source_selections_delete AFTER DELETE ON source_units
BEGIN INSERT INTO derivation_input_versions VALUES ('source_selections','all',1) ON CONFLICT(namespace,input_key) DO UPDATE SET version=version+1; END;
CREATE TRIGGER derivation_calendar_raw_delete AFTER DELETE ON observations WHEN OLD.scope_kind='calendar'
BEGIN INSERT INTO derivation_input_versions VALUES ('calendar_raw','all',1) ON CONFLICT(namespace,input_key) DO UPDATE SET version=version+1; END;
CREATE TRIGGER derivation_index_raw_delete AFTER DELETE ON observations WHEN OLD.scope_kind='source_index'
BEGIN INSERT INTO derivation_input_versions VALUES ('index_raw','all',1) ON CONFLICT(namespace,input_key) DO UPDATE SET version=version+1; END;
CREATE TRIGGER derivation_history_findings_delete AFTER DELETE ON findings WHEN OLD.owner_kind IN ('history_year','phase1_year')
BEGIN INSERT INTO derivation_input_versions VALUES ('history_findings','all',1) ON CONFLICT(namespace,input_key) DO UPDATE SET version=version+1; END;
CREATE TRIGGER derivation_snapshot_basis_delete AFTER DELETE ON snapshots
BEGIN INSERT INTO derivation_input_versions VALUES ('snapshot_basis','all',1) ON CONFLICT(namespace,input_key) DO UPDATE SET version=version+1; END;
CREATE TRIGGER derivation_canonical_scope_rows_insert AFTER INSERT ON canonical_scope_rows
BEGIN INSERT OR IGNORE INTO derivation_scopes(stage,unit_kind,unit_id,registered_at) SELECT 'project',NEW.scope_kind,NEW.scope_id,strftime('%Y-%m-%dT%H:%M:%f+00:00','now') WHERE NEW.scope_kind IN ('calendar','source_index','dancer','event','source_event','inventory','map','history') AND NOT EXISTS (SELECT 1 FROM derivation_scopes WHERE stage='project' AND unit_kind=NEW.scope_kind AND unit_id=NEW.scope_id); END;
CREATE TRIGGER derivation_source_event_scope_rows_insert AFTER INSERT ON source_event_scope_rows
BEGIN INSERT OR IGNORE INTO derivation_scopes(stage,unit_kind,unit_id,registered_at) SELECT 'project','source_index',NEW.scope_id,strftime('%Y-%m-%dT%H:%M:%f+00:00','now') WHERE 'source_index' IN ('calendar','source_index','dancer','event','source_event','inventory','map','history') AND NOT EXISTS (SELECT 1 FROM derivation_scopes WHERE stage='project' AND unit_kind='source_index' AND unit_id=NEW.scope_id); END;
CREATE INDEX derivation_scopes_unassessed ON derivation_scopes(stage,unit_kind,materialized_generation_id,unit_id);
CREATE TRIGGER derivation_observation_replace BEFORE INSERT ON observations
BEGIN
 INSERT INTO derivation_input_versions(namespace,input_key,version)
 SELECT 'raw',json_array(scope_kind,scope_id),1 FROM observations
 WHERE observation_id=NEW.observation_id OR (watch_id=NEW.watch_id AND snapshot_id=NEW.snapshot_id AND kind=NEW.kind AND seq=NEW.seq)
 ON CONFLICT(namespace,input_key) DO UPDATE SET version=version+1;
END;
CREATE TRIGGER derivation_migrations_insert AFTER INSERT ON identity_reference_migrations
BEGIN
 INSERT INTO derivation_input_versions VALUES ('reference_migrations','all',1)
 ON CONFLICT(namespace,input_key) DO UPDATE SET version=version+1;
END;
CREATE TRIGGER "publication_fence_derivation_scopes_insert" BEFORE INSERT ON "derivation_scopes" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_derivation_scopes_update" BEFORE UPDATE ON "derivation_scopes" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_derivation_scopes_delete" BEFORE DELETE ON "derivation_scopes" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_narrow_fence_derivation_scopes_insert" BEFORE INSERT ON "derivation_scopes" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind='publication_narrow') AND (NEW.stage='build' AND NOT EXISTS(SELECT 1 FROM "derivation_scopes" k WHERE k."stage"=NEW."stage" AND k."unit_kind"=NEW."unit_kind" AND k."unit_id"=NEW."unit_id")) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_narrow_fence_derivation_scopes_update" BEFORE UPDATE ON "derivation_scopes" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind='publication_narrow') AND ((OLD.stage='build' OR NEW.stage='build')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_narrow_fence_derivation_scopes_delete" BEFORE DELETE ON "derivation_scopes" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind='publication_narrow') AND (OLD.stage='build') BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_derivation_dependency_sets_insert" BEFORE INSERT ON "derivation_dependency_sets" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_derivation_dependency_sets_update" BEFORE UPDATE ON "derivation_dependency_sets" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_derivation_generations_insert" BEFORE INSERT ON "derivation_generations" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_derivation_generations_delete" BEFORE DELETE ON "derivation_generations" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_narrow_fence_derivation_generations_insert" BEFORE INSERT ON "derivation_generations" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind='publication_narrow') AND (NEW.stage='build' AND NOT EXISTS(SELECT 1 FROM "derivation_generations" k WHERE k."generation_id"=NEW."generation_id")) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_narrow_fence_derivation_generations_delete" BEFORE DELETE ON "derivation_generations" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind='publication_narrow') AND (OLD.stage='build') BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_derivation_input_versions_insert" BEFORE INSERT ON "derivation_input_versions" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_derivation_input_versions_update" BEFORE UPDATE ON "derivation_input_versions" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_derivation_input_versions_delete" BEFORE DELETE ON "derivation_input_versions" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE INDEX history_origin_intents_event ON history_origin_intents(source,event_id);
CREATE INDEX history_origin_intents_run ON history_origin_intents(dispatch_run_id);
CREATE INDEX history_origin_requests_cycle ON history_origin_requests(run_id,source,event_id);
CREATE INDEX history_origin_requests_day ON history_origin_requests(source,day,event_id);
CREATE INDEX history_origin_requests_watch ON history_origin_requests(watch_id);
CREATE TRIGGER "publication_fence_history_origin_intents_insert" BEFORE INSERT ON "history_origin_intents" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_history_origin_intents_update" BEFORE UPDATE ON "history_origin_intents" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_history_origin_intents_delete" BEFORE DELETE ON "history_origin_intents" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_history_origin_requests_insert" BEFORE INSERT ON "history_origin_requests" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_history_origin_requests_update" BEFORE UPDATE ON "history_origin_requests" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_history_origin_requests_delete" BEFORE DELETE ON "history_origin_requests" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_history_origin_operator_refs_insert" BEFORE INSERT ON "history_origin_operator_refs" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_history_origin_operator_refs_update" BEFORE UPDATE ON "history_origin_operator_refs" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_history_origin_operator_refs_delete" BEFORE DELETE ON "history_origin_operator_refs" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE INDEX source_event_enumerations_event ON source_event_enumerations(source,source_ref);
CREATE INDEX source_event_enumerations_parent ON source_event_enumerations(generation_id);
CREATE INDEX source_event_inventory_enumeration ON source_event_inventory(enumeration_id);
CREATE INDEX source_event_enumerations_decision ON source_event_enumerations(decision_id);
CREATE INDEX source_event_enumerations_predecessor ON source_event_enumerations(predecessor_id);
CREATE INDEX event_enumeration_members_request ON source_event_enumeration_members(request_id);
CREATE INDEX event_enumeration_inputs_decision ON event_enumeration_inputs(decision_id);
CREATE TRIGGER event_enumeration_no_update BEFORE UPDATE ON source_event_enumerations
BEGIN SELECT RAISE(ABORT,'event enumerations are immutable'); END;
CREATE TRIGGER event_enumeration_no_delete BEFORE DELETE ON source_event_enumerations
BEGIN SELECT RAISE(ABORT,'event enumerations are immutable'); END;
CREATE TRIGGER event_enumeration_member_no_update BEFORE UPDATE ON source_event_enumeration_members
BEGIN SELECT RAISE(ABORT,'event enumeration members are immutable'); END;
CREATE TRIGGER event_enumeration_member_no_delete BEFORE DELETE ON source_event_enumeration_members
BEGIN SELECT RAISE(ABORT,'event enumeration members are immutable'); END;
CREATE TRIGGER event_enumeration_input_no_update BEFORE UPDATE ON event_enumeration_inputs
BEGIN SELECT RAISE(ABORT,'enumeration bootstrap receipts are immutable'); END;
CREATE TRIGGER event_enumeration_input_no_delete BEFORE DELETE ON event_enumeration_inputs
BEGIN SELECT RAISE(ABORT,'enumeration bootstrap receipts are immutable'); END;
CREATE INDEX source_event_member_watches_watch ON source_event_member_watches(watch_id,enumeration_id);
CREATE TRIGGER event_member_watch_no_update BEFORE UPDATE ON source_event_member_watches
BEGIN SELECT RAISE(ABORT,'event membership watch support is immutable'); END;
CREATE TRIGGER event_member_watch_no_delete BEFORE DELETE ON source_event_member_watches
BEGIN SELECT RAISE(ABORT,'event membership watch support is immutable'); END;
CREATE TRIGGER "publication_fence_source_event_inventory_insert" BEFORE INSERT ON "source_event_inventory" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_source_event_inventory_update" BEFORE UPDATE ON "source_event_inventory" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_source_event_inventory_delete" BEFORE DELETE ON "source_event_inventory" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_source_event_enumerations_insert" BEFORE INSERT ON "source_event_enumerations" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_source_event_enumerations_update" BEFORE UPDATE ON "source_event_enumerations" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_source_event_enumerations_delete" BEFORE DELETE ON "source_event_enumerations" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_source_event_enumeration_members_insert" BEFORE INSERT ON "source_event_enumeration_members" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_source_event_enumeration_members_update" BEFORE UPDATE ON "source_event_enumeration_members" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_source_event_enumeration_members_delete" BEFORE DELETE ON "source_event_enumeration_members" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_event_enumeration_inputs_insert" BEFORE INSERT ON "event_enumeration_inputs" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_event_enumeration_inputs_update" BEFORE UPDATE ON "event_enumeration_inputs" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_event_enumeration_inputs_delete" BEFORE DELETE ON "event_enumeration_inputs" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_source_event_member_watches_insert" BEFORE INSERT ON "source_event_member_watches" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_source_event_member_watches_update" BEFORE UPDATE ON "source_event_member_watches" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_source_event_member_watches_delete" BEFORE DELETE ON "source_event_member_watches" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE INDEX scheduler_event_requests_owner ON scheduler_event_requests(selected_host,category,owner_key);
CREATE INDEX scheduler_event_requests_event ON scheduler_event_requests(source,source_ref,action_id);
CREATE INDEX scheduler_event_turns_event ON scheduler_event_turns(source,source_ref);
CREATE INDEX scheduler_event_turns_policy ON scheduler_event_turns(policy_digest);
CREATE INDEX scheduler_event_requests_policy ON scheduler_event_requests(policy_digest);
CREATE INDEX scheduler_event_requests_enumeration ON scheduler_event_requests(enumeration_id);
CREATE TRIGGER "publication_fence_scheduler_event_policies_insert" BEFORE INSERT ON "scheduler_event_policies" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_scheduler_event_policies_update" BEFORE UPDATE ON "scheduler_event_policies" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_scheduler_event_policies_delete" BEFORE DELETE ON "scheduler_event_policies" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_scheduler_event_turns_insert" BEFORE INSERT ON "scheduler_event_turns" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_scheduler_event_turns_update" BEFORE UPDATE ON "scheduler_event_turns" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_scheduler_event_turns_delete" BEFORE DELETE ON "scheduler_event_turns" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_scheduler_event_requests_insert" BEFORE INSERT ON "scheduler_event_requests" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_scheduler_event_requests_update" BEFORE UPDATE ON "scheduler_event_requests" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_scheduler_event_requests_delete" BEFORE DELETE ON "scheduler_event_requests" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE INDEX scheduler_capacity_requests_host ON scheduler_capacity_requests(selected_host,lane);
CREATE INDEX scheduler_capacity_requests_policy ON scheduler_capacity_requests(policy_digest);
CREATE TRIGGER "publication_fence_scheduler_capacity_service_insert" BEFORE INSERT ON "scheduler_capacity_service" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_scheduler_capacity_service_update" BEFORE UPDATE ON "scheduler_capacity_service" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_scheduler_capacity_service_delete" BEFORE DELETE ON "scheduler_capacity_service" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_scheduler_capacity_policies_insert" BEFORE INSERT ON "scheduler_capacity_policies" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_scheduler_capacity_policies_update" BEFORE UPDATE ON "scheduler_capacity_policies" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_scheduler_capacity_policies_delete" BEFORE DELETE ON "scheduler_capacity_policies" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_scheduler_capacity_requests_insert" BEFORE INSERT ON "scheduler_capacity_requests" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_scheduler_capacity_requests_update" BEFORE UPDATE ON "scheduler_capacity_requests" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_scheduler_capacity_requests_delete" BEFORE DELETE ON "scheduler_capacity_requests" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE INDEX event_pressure_subjects_refresh ON event_pressure_subjects(checked_sequence,source,source_ref);
CREATE INDEX event_pressure_watches_subject ON event_pressure_watches(source,source_ref);
CREATE INDEX event_pressure_hosts_subject ON event_pressure_hosts(source,source_ref);
CREATE INDEX event_pressure_parents_event ON event_pressure_parents(source,source_ref);
CREATE TRIGGER pressure_inventory_insert AFTER INSERT ON source_event_inventory BEGIN
    INSERT INTO event_pressure_parents
        SELECT DISTINCT u.watch_id,NEW.source,NEW.source_ref FROM source_event_enumerations e
        JOIN json_each(e.parent_support_json) parent JOIN source_generations g ON g.generation_id=parent.key
        JOIN source_units u USING(unit_key) WHERE e.enumeration_id=NEW.enumeration_id
        ON CONFLICT(watch_id,source,source_ref) DO NOTHING;
END;
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
CREATE TRIGGER "publication_fence_event_pressure_state_insert" BEFORE INSERT ON "event_pressure_state" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_event_pressure_state_update" BEFORE UPDATE ON "event_pressure_state" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_event_pressure_state_delete" BEFORE DELETE ON "event_pressure_state" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_event_pressure_subjects_insert" BEFORE INSERT ON "event_pressure_subjects" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_event_pressure_subjects_update" BEFORE UPDATE ON "event_pressure_subjects" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_event_pressure_subjects_delete" BEFORE DELETE ON "event_pressure_subjects" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_event_pressure_watches_insert" BEFORE INSERT ON "event_pressure_watches" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_event_pressure_watches_update" BEFORE UPDATE ON "event_pressure_watches" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_event_pressure_watches_delete" BEFORE DELETE ON "event_pressure_watches" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_event_pressure_hosts_insert" BEFORE INSERT ON "event_pressure_hosts" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_event_pressure_hosts_update" BEFORE UPDATE ON "event_pressure_hosts" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_event_pressure_hosts_delete" BEFORE DELETE ON "event_pressure_hosts" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_event_pressure_latches_insert" BEFORE INSERT ON "event_pressure_latches" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_event_pressure_latches_update" BEFORE UPDATE ON "event_pressure_latches" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_event_pressure_latches_delete" BEFORE DELETE ON "event_pressure_latches" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_event_pressure_dirty_insert" BEFORE INSERT ON "event_pressure_dirty" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_event_pressure_dirty_update" BEFORE UPDATE ON "event_pressure_dirty" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_event_pressure_dirty_delete" BEFORE DELETE ON "event_pressure_dirty" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_event_pressure_parents_insert" BEFORE INSERT ON "event_pressure_parents" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_event_pressure_parents_update" BEFORE UPDATE ON "event_pressure_parents" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_event_pressure_parents_delete" BEFORE DELETE ON "event_pressure_parents" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE INDEX event_blocker_refreshes_run ON event_blocker_refreshes(run_id,refresh_id);
CREATE INDEX event_blocker_observations_event ON event_blocker_observations(source,source_ref,observation_id);
CREATE INDEX event_blocker_observations_refresh ON event_blocker_observations(refresh_id);
CREATE INDEX event_blocker_observations_policy ON event_blocker_observations(policy_digest);
CREATE INDEX event_blocker_latest_observation ON event_blocker_latest(observation_id);
CREATE INDEX watches_event_blockers ON watches(source,source_ref,watch_id);
CREATE TRIGGER event_blocker_observation_no_update BEFORE UPDATE ON event_blocker_observations
BEGIN SELECT RAISE(ABORT,'blocker observations are immutable'); END;
CREATE TRIGGER event_blocker_observation_no_delete BEFORE DELETE ON event_blocker_observations
BEGIN SELECT RAISE(ABORT,'blocker observations are immutable'); END;
CREATE TRIGGER event_blocker_refresh_no_update BEFORE UPDATE ON event_blocker_refreshes
BEGIN SELECT RAISE(ABORT,'blocker refresh summaries are immutable'); END;
CREATE TRIGGER event_blocker_refresh_no_delete BEFORE DELETE ON event_blocker_refreshes
BEGIN SELECT RAISE(ABORT,'blocker refresh summaries are immutable'); END;
CREATE TRIGGER event_blocker_policy_no_update BEFORE UPDATE ON event_blocker_policies
BEGIN SELECT RAISE(ABORT,'blocker policies are immutable'); END;
CREATE TRIGGER event_blocker_policy_no_delete BEFORE DELETE ON event_blocker_policies
BEGIN SELECT RAISE(ABORT,'blocker policies are immutable'); END;
CREATE TRIGGER "publication_fence_event_blocker_policies_insert" BEFORE INSERT ON "event_blocker_policies" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_event_blocker_policies_update" BEFORE UPDATE ON "event_blocker_policies" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_event_blocker_policies_delete" BEFORE DELETE ON "event_blocker_policies" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_event_blocker_refreshes_insert" BEFORE INSERT ON "event_blocker_refreshes" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_event_blocker_refreshes_update" BEFORE UPDATE ON "event_blocker_refreshes" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_event_blocker_refreshes_delete" BEFORE DELETE ON "event_blocker_refreshes" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_event_blocker_observations_insert" BEFORE INSERT ON "event_blocker_observations" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_event_blocker_observations_update" BEFORE UPDATE ON "event_blocker_observations" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_event_blocker_observations_delete" BEFORE DELETE ON "event_blocker_observations" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_event_blocker_latest_insert" BEFORE INSERT ON "event_blocker_latest" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_event_blocker_latest_update" BEFORE UPDATE ON "event_blocker_latest" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_event_blocker_latest_delete" BEFORE DELETE ON "event_blocker_latest" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_event_blocker_cursor_insert" BEFORE INSERT ON "event_blocker_cursor" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_event_blocker_cursor_update" BEFORE UPDATE ON "event_blocker_cursor" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_event_blocker_cursor_delete" BEFORE DELETE ON "event_blocker_cursor" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE INDEX event_stage_operations_run ON event_stage_operations(run_id);
CREATE INDEX event_stage_operations_request ON event_stage_operations(source,stage,request_id,operation_id);
CREATE INDEX event_stage_operations_source ON event_stage_operations(source,stage,operation_id);
CREATE INDEX event_stage_operations_watch ON event_stage_operations(watch_id,operation_id);
CREATE INDEX event_progress_observations_enumeration ON event_progress_observations(enumeration_id);
CREATE INDEX event_progress_receipts_event ON event_progress_receipts(source,source_ref,receipt_id);
CREATE INDEX event_progress_receipts_operation ON event_progress_receipts(operation_id);
CREATE INDEX event_progress_receipts_enumeration ON event_progress_receipts(enumeration_id);
CREATE TRIGGER event_stage_operation_no_update BEFORE UPDATE ON event_stage_operations
BEGIN SELECT RAISE(ABORT,'event stage operations are immutable'); END;
CREATE TRIGGER event_stage_operation_no_delete BEFORE DELETE ON event_stage_operations
BEGIN SELECT RAISE(ABORT,'event stage operations are immutable'); END;
CREATE TRIGGER event_progress_receipt_no_update BEFORE UPDATE ON event_progress_receipts
BEGIN SELECT RAISE(ABORT,'event progress receipts are immutable'); END;
CREATE TRIGGER event_progress_receipt_no_delete BEFORE DELETE ON event_progress_receipts
BEGIN SELECT RAISE(ABORT,'event progress receipts are immutable'); END;
CREATE TRIGGER event_progress_policy_no_update BEFORE UPDATE ON event_progress_policies
BEGIN SELECT RAISE(ABORT,'event progress policies are immutable'); END;
CREATE TRIGGER event_progress_policy_no_delete BEFORE DELETE ON event_progress_policies
BEGIN SELECT RAISE(ABORT,'event progress policies are immutable'); END;
CREATE TRIGGER event_progress_current_member_insert AFTER INSERT ON source_event_enumeration_members
WHEN EXISTS(SELECT 1 FROM source_event_inventory WHERE enumeration_id=NEW.enumeration_id)
BEGIN UPDATE event_pressure_state SET epoch=epoch+1; END;
CREATE TRIGGER "publication_fence_event_stage_operations_insert" BEFORE INSERT ON "event_stage_operations" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_event_stage_operations_update" BEFORE UPDATE ON "event_stage_operations" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_event_stage_operations_delete" BEFORE DELETE ON "event_stage_operations" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_event_progress_policies_insert" BEFORE INSERT ON "event_progress_policies" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_event_progress_policies_update" BEFORE UPDATE ON "event_progress_policies" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_event_progress_policies_delete" BEFORE DELETE ON "event_progress_policies" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_event_progress_observations_insert" BEFORE INSERT ON "event_progress_observations" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_event_progress_observations_update" BEFORE UPDATE ON "event_progress_observations" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_event_progress_observations_delete" BEFORE DELETE ON "event_progress_observations" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_event_progress_receipts_insert" BEFORE INSERT ON "event_progress_receipts" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_event_progress_receipts_update" BEFORE UPDATE ON "event_progress_receipts" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_event_progress_receipts_delete" BEFORE DELETE ON "event_progress_receipts" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_event_progress_scans_insert" BEFORE INSERT ON "event_progress_scans" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_event_progress_scans_update" BEFORE UPDATE ON "event_progress_scans" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_event_progress_scans_delete" BEFORE DELETE ON "event_progress_scans" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_event_progress_cursor_insert" BEFORE INSERT ON "event_progress_cursor" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_event_progress_cursor_update" BEFORE UPDATE ON "event_progress_cursor" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_event_progress_cursor_delete" BEFORE DELETE ON "event_progress_cursor" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE INDEX event_accounting_support_enumeration ON event_accounting_support_observations(enumeration_id);
CREATE INDEX event_accounting_receipts_event ON event_accounting_receipts(source,source_ref,receipt_id);
CREATE INDEX event_accounting_receipts_definite ON event_accounting_receipts(source,source_ref,receipt_id) WHERE assessment!='unassessed';
CREATE INDEX event_accounting_receipts_definite_enum ON event_accounting_receipts(source,source_ref,enumeration_id,receipt_id) WHERE assessment!='unassessed';
CREATE INDEX event_accounting_receipts_previous ON event_accounting_receipts(previous_receipt_id);
CREATE TRIGGER event_accounting_receipt_no_update BEFORE UPDATE ON event_accounting_receipts
BEGIN SELECT RAISE(ABORT,'event accounting receipts are immutable'); END;
CREATE TRIGGER event_accounting_receipt_no_delete BEFORE DELETE ON event_accounting_receipts
BEGIN SELECT RAISE(ABORT,'event accounting receipts are immutable'); END;
CREATE TRIGGER "publication_fence_event_accounting_support_observations_insert" BEFORE INSERT ON "event_accounting_support_observations" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_event_accounting_support_observations_update" BEFORE UPDATE ON "event_accounting_support_observations" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_event_accounting_support_observations_delete" BEFORE DELETE ON "event_accounting_support_observations" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_event_accounting_receipts_insert" BEFORE INSERT ON "event_accounting_receipts" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_event_accounting_receipts_update" BEFORE UPDATE ON "event_accounting_receipts" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_event_accounting_receipts_delete" BEFORE DELETE ON "event_accounting_receipts" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE INDEX event_retirement_observations_enumeration ON event_retirement_observations(enumeration_id);
CREATE INDEX event_retirement_observations_predecessor ON event_retirement_observations(predecessor_id);
CREATE INDEX event_retirement_receipts_event ON event_retirement_receipts(source,source_ref,receipt_id);
CREATE TRIGGER event_retirement_receipt_no_update BEFORE UPDATE ON event_retirement_receipts
BEGIN SELECT RAISE(ABORT,'event retirement receipts are immutable'); END;
CREATE TRIGGER event_retirement_receipt_no_delete BEFORE DELETE ON event_retirement_receipts
BEGIN SELECT RAISE(ABORT,'event retirement receipts are immutable'); END;
CREATE TRIGGER "publication_fence_event_retirement_observations_insert" BEFORE INSERT ON "event_retirement_observations" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_event_retirement_observations_update" BEFORE UPDATE ON "event_retirement_observations" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_event_retirement_observations_delete" BEFORE DELETE ON "event_retirement_observations" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_event_retirement_receipts_insert" BEFORE INSERT ON "event_retirement_receipts" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_event_retirement_receipts_update" BEFORE UPDATE ON "event_retirement_receipts" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_event_retirement_receipts_delete" BEFORE DELETE ON "event_retirement_receipts" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE INDEX event_gap_observations_enumeration ON event_gap_observations(enumeration_id);
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
CREATE TRIGGER gap_snapshot_support_rewrite
AFTER UPDATE OF classification,via,fetched_at,http_status,captured_at ON snapshots
WHEN OLD.classification IS NOT NEW.classification OR OLD.via IS NOT NEW.via
 OR OLD.fetched_at IS NOT NEW.fetched_at OR OLD.http_status IS NOT NEW.http_status
 OR OLD.captured_at IS NOT NEW.captured_at BEGIN
    UPDATE event_pressure_state SET epoch=epoch+1;
END;
CREATE TRIGGER "publication_fence_event_gap_revisions_insert" BEFORE INSERT ON "event_gap_revisions" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_event_gap_revisions_update" BEFORE UPDATE ON "event_gap_revisions" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_event_gap_revisions_delete" BEFORE DELETE ON "event_gap_revisions" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_event_gap_observations_insert" BEFORE INSERT ON "event_gap_observations" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_event_gap_observations_update" BEFORE UPDATE ON "event_gap_observations" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_event_gap_observations_delete" BEFORE DELETE ON "event_gap_observations" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
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
CREATE TRIGGER "publication_fence_event_timing_insert" BEFORE INSERT ON "event_timing" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_event_timing_update" BEFORE UPDATE ON "event_timing" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_event_timing_delete" BEFORE DELETE ON "event_timing" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_event_timing_history_insert" BEFORE INSERT ON "event_timing_history" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_event_timing_history_update" BEFORE UPDATE ON "event_timing_history" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_event_timing_history_delete" BEFORE DELETE ON "event_timing_history" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_event_timing_cursor_insert" BEFORE INSERT ON "event_timing_cursor" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_event_timing_cursor_update" BEFORE UPDATE ON "event_timing_cursor" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_event_timing_cursor_delete" BEFORE DELETE ON "event_timing_cursor" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE INDEX source_event_retirement_receipts_event
ON source_event_retirement_receipts(source,source_ref,receipt_id);
CREATE TRIGGER source_event_retirement_no_update BEFORE UPDATE ON source_event_retirement_receipts
BEGIN SELECT RAISE(ABORT,'source event retirement receipts are immutable'); END;
CREATE TRIGGER source_event_retirement_no_delete BEFORE DELETE ON source_event_retirement_receipts
BEGIN SELECT RAISE(ABORT,'source event retirement receipts are immutable'); END;
CREATE TRIGGER source_event_retirement_proof_no_update BEFORE UPDATE ON source_event_retirement_proofs
BEGIN SELECT RAISE(ABORT,'source event retirement proofs are immutable'); END;
CREATE TRIGGER source_event_retirement_proof_no_delete BEFORE DELETE ON source_event_retirement_proofs
BEGIN SELECT RAISE(ABORT,'source event retirement proofs are immutable'); END;
CREATE INDEX source_generations_retained_source ON source_generations(
    CASE WHEN json_valid(recipe_json) THEN
        CASE WHEN json_type(recipe_json,'$.context.source')='text'
             THEN json_extract(recipe_json,'$.context.source') END
    END
);
CREATE TRIGGER "publication_fence_source_event_retirement_receipts_insert" BEFORE INSERT ON "source_event_retirement_receipts" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_source_event_retirement_receipts_update" BEFORE UPDATE ON "source_event_retirement_receipts" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_source_event_retirement_receipts_delete" BEFORE DELETE ON "source_event_retirement_receipts" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_source_event_retirement_proofs_insert" BEFORE INSERT ON "source_event_retirement_proofs" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_source_event_retirement_proofs_update" BEFORE UPDATE ON "source_event_retirement_proofs" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_source_event_retirement_proofs_delete" BEFORE DELETE ON "source_event_retirement_proofs" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER host_spacing_baselines_no_update BEFORE UPDATE ON host_request_spacing_baselines
BEGIN SELECT RAISE(ABORT,'request spacing baselines are immutable'); END;
CREATE TRIGGER host_spacing_baselines_no_delete BEFORE DELETE ON host_request_spacing_baselines
BEGIN SELECT RAISE(ABORT,'request spacing baselines are immutable'); END;
CREATE TRIGGER "publication_fence_host_request_spacing_insert" BEFORE INSERT ON "host_request_spacing" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_host_request_spacing_update" BEFORE UPDATE ON "host_request_spacing" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_host_request_spacing_delete" BEFORE DELETE ON "host_request_spacing" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_host_request_spacing_baselines_insert" BEFORE INSERT ON "host_request_spacing_baselines" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_host_request_spacing_baselines_update" BEFORE UPDATE ON "host_request_spacing_baselines" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_host_request_spacing_baselines_delete" BEFORE DELETE ON "host_request_spacing_baselines" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER history_timing_accepted_inputs_insert AFTER INSERT ON accepted_inputs BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_accepted_inputs_update AFTER UPDATE ON accepted_inputs BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_accepted_inputs_delete AFTER DELETE ON accepted_inputs BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_admission_decisions_insert AFTER INSERT ON admission_decisions BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_admission_decisions_update AFTER UPDATE ON admission_decisions BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_admission_decisions_delete AFTER DELETE ON admission_decisions BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_admission_policies_insert AFTER INSERT ON admission_policies BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_admission_policies_update AFTER UPDATE ON admission_policies BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_admission_policies_delete AFTER DELETE ON admission_policies BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_admission_reviews_insert AFTER INSERT ON admission_reviews BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_admission_reviews_update AFTER UPDATE ON admission_reviews BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_admission_reviews_delete AFTER DELETE ON admission_reviews BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_archive_captures_insert AFTER INSERT ON archive_captures BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_archive_captures_update AFTER UPDATE ON archive_captures BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_archive_captures_delete AFTER DELETE ON archive_captures BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_archive_queries_insert AFTER INSERT ON archive_queries BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_archive_queries_update AFTER UPDATE ON archive_queries BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_archive_queries_delete AFTER DELETE ON archive_queries BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_callback_marks_insert AFTER INSERT ON callback_marks BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_callback_marks_update AFTER UPDATE ON callback_marks BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_callback_marks_delete AFTER DELETE ON callback_marks BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_callbacks_insert AFTER INSERT ON callbacks BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_callbacks_update AFTER UPDATE ON callbacks BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_callbacks_delete AFTER DELETE ON callbacks BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_canonical_scope_rows_insert AFTER INSERT ON canonical_scope_rows BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_canonical_scope_rows_update AFTER UPDATE ON canonical_scope_rows BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_canonical_scope_rows_delete AFTER DELETE ON canonical_scope_rows BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_contests_insert AFTER INSERT ON contests BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_contests_update AFTER UPDATE ON contests BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_contests_delete AFTER DELETE ON contests BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_coverage_insert AFTER INSERT ON coverage BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_coverage_update AFTER UPDATE ON coverage BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_coverage_delete AFTER DELETE ON coverage BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_cursors_insert AFTER INSERT ON cursors BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_cursors_update AFTER UPDATE ON cursors BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_cursors_delete AFTER DELETE ON cursors BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_dancers_insert AFTER INSERT ON dancers BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_dancers_update AFTER UPDATE ON dancers BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_dancers_delete AFTER DELETE ON dancers BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_derivation_dependency_sets_insert AFTER INSERT ON derivation_dependency_sets BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_derivation_dependency_sets_update AFTER UPDATE ON derivation_dependency_sets BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_derivation_dependency_sets_delete AFTER DELETE ON derivation_dependency_sets BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_derivation_generations_insert AFTER INSERT ON derivation_generations BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_derivation_generations_update AFTER UPDATE ON derivation_generations BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_derivation_generations_delete AFTER DELETE ON derivation_generations BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_derivation_input_versions_insert AFTER INSERT ON derivation_input_versions BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_derivation_input_versions_update AFTER UPDATE ON derivation_input_versions BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_derivation_input_versions_delete AFTER DELETE ON derivation_input_versions BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_derivation_scopes_insert AFTER INSERT ON derivation_scopes BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_derivation_scopes_update AFTER UPDATE ON derivation_scopes BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_derivation_scopes_delete AFTER DELETE ON derivation_scopes BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_entries_insert AFTER INSERT ON entries BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_entries_update AFTER UPDATE ON entries BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_entries_delete AFTER DELETE ON entries BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_events_insert AFTER INSERT ON events BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_events_update AFTER UPDATE ON events BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_events_delete AFTER DELETE ON events BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_final_marks_insert AFTER INSERT ON final_marks BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_final_marks_update AFTER UPDATE ON final_marks BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_final_marks_delete AFTER DELETE ON final_marks BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_finding_support_insert AFTER INSERT ON finding_support BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_finding_support_update AFTER UPDATE ON finding_support BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_finding_support_delete AFTER DELETE ON finding_support BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_findings_insert AFTER INSERT ON findings BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_findings_update AFTER UPDATE ON findings BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_findings_delete AFTER DELETE ON findings BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_heats_insert AFTER INSERT ON heats BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_heats_update AFTER UPDATE ON heats BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_heats_delete AFTER DELETE ON heats BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_history_acceptance_insert AFTER INSERT ON history_acceptance BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_history_acceptance_update AFTER UPDATE ON history_acceptance BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_history_acceptance_delete AFTER DELETE ON history_acceptance BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_identity_decisions_insert AFTER INSERT ON identity_decisions BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_identity_decisions_update AFTER UPDATE ON identity_decisions BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_identity_decisions_delete AFTER DELETE ON identity_decisions BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_identity_journal_acceptances_insert AFTER INSERT ON identity_journal_acceptances BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_identity_journal_acceptances_update AFTER UPDATE ON identity_journal_acceptances BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_identity_journal_acceptances_delete AFTER DELETE ON identity_journal_acceptances BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_identity_link_history_insert AFTER INSERT ON identity_link_history BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_identity_link_history_update AFTER UPDATE ON identity_link_history BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_identity_link_history_delete AFTER DELETE ON identity_link_history BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_identity_link_resolutions_insert AFTER INSERT ON identity_link_resolutions BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_identity_link_resolutions_update AFTER UPDATE ON identity_link_resolutions BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_identity_link_resolutions_delete AFTER DELETE ON identity_link_resolutions BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_identity_links_insert AFTER INSERT ON identity_links BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_identity_links_update AFTER UPDATE ON identity_links BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_identity_links_delete AFTER DELETE ON identity_links BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_identity_reference_bindings_insert AFTER INSERT ON identity_reference_bindings BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_identity_reference_bindings_update AFTER UPDATE ON identity_reference_bindings BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_identity_reference_bindings_delete AFTER DELETE ON identity_reference_bindings BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_identity_reference_migrations_insert AFTER INSERT ON identity_reference_migrations BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_identity_reference_migrations_update AFTER UPDATE ON identity_reference_migrations BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_identity_reference_migrations_delete AFTER DELETE ON identity_reference_migrations BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_identity_source_refs_insert AFTER INSERT ON identity_source_refs BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_identity_source_refs_update AFTER UPDATE ON identity_source_refs BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_identity_source_refs_delete AFTER DELETE ON identity_source_refs BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_judges_insert AFTER INSERT ON judges BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_judges_update AFTER UPDATE ON judges BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_judges_delete AFTER DELETE ON judges BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_link_candidates_insert AFTER INSERT ON link_candidates BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_link_candidates_update AFTER UPDATE ON link_candidates BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_link_candidates_delete AFTER DELETE ON link_candidates BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_meta_insert AFTER INSERT ON meta BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_meta_update AFTER UPDATE ON meta BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_meta_delete AFTER DELETE ON meta BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_observations_insert AFTER INSERT ON observations BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_observations_update AFTER UPDATE ON observations BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_observations_delete AFTER DELETE ON observations BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_pending_work_insert AFTER INSERT ON pending_work BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_pending_work_update AFTER UPDATE ON pending_work BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_pending_work_delete AFTER DELETE ON pending_work BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_placements_insert AFTER INSERT ON placements BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_placements_update AFTER UPDATE ON placements BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_placements_delete AFTER DELETE ON placements BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_registry_placements_insert AFTER INSERT ON registry_placements BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_registry_placements_update AFTER UPDATE ON registry_placements BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_registry_placements_delete AFTER DELETE ON registry_placements BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_registry_verifications_insert AFTER INSERT ON registry_verifications BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_registry_verifications_update AFTER UPDATE ON registry_verifications BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_registry_verifications_delete AFTER DELETE ON registry_verifications BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_revisions_insert AFTER INSERT ON revisions BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_revisions_update AFTER UPDATE ON revisions BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_revisions_delete AFTER DELETE ON revisions BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_rounds_insert AFTER INSERT ON rounds BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_rounds_update AFTER UPDATE ON rounds BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_rounds_delete AFTER DELETE ON rounds BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_series_insert AFTER INSERT ON series BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_series_update AFTER UPDATE ON series BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_series_delete AFTER DELETE ON series BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_snapshots_insert AFTER INSERT ON snapshots BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_snapshots_update AFTER UPDATE ON snapshots BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_snapshots_delete AFTER DELETE ON snapshots BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_source_event_enumeration_members_insert AFTER INSERT ON source_event_enumeration_members BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_source_event_enumeration_members_update AFTER UPDATE ON source_event_enumeration_members BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_source_event_enumeration_members_delete AFTER DELETE ON source_event_enumeration_members BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_source_event_enumerations_insert AFTER INSERT ON source_event_enumerations BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_source_event_enumerations_update AFTER UPDATE ON source_event_enumerations BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_source_event_enumerations_delete AFTER DELETE ON source_event_enumerations BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_source_event_inventory_insert AFTER INSERT ON source_event_inventory BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_source_event_inventory_update AFTER UPDATE ON source_event_inventory BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_source_event_inventory_delete AFTER DELETE ON source_event_inventory BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_source_event_map_insert AFTER INSERT ON source_event_map BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_source_event_map_update AFTER UPDATE ON source_event_map BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_source_event_map_delete AFTER DELETE ON source_event_map BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_source_event_member_watches_insert AFTER INSERT ON source_event_member_watches BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_source_event_member_watches_update AFTER UPDATE ON source_event_member_watches BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_source_event_member_watches_delete AFTER DELETE ON source_event_member_watches BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_source_event_retirement_proofs_insert AFTER INSERT ON source_event_retirement_proofs BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_source_event_retirement_proofs_update AFTER UPDATE ON source_event_retirement_proofs BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_source_event_retirement_proofs_delete AFTER DELETE ON source_event_retirement_proofs BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_source_event_retirement_receipts_insert AFTER INSERT ON source_event_retirement_receipts BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_source_event_retirement_receipts_update AFTER UPDATE ON source_event_retirement_receipts BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_source_event_retirement_receipts_delete AFTER DELETE ON source_event_retirement_receipts BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_source_event_scope_rows_insert AFTER INSERT ON source_event_scope_rows BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_source_event_scope_rows_update AFTER UPDATE ON source_event_scope_rows BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_source_event_scope_rows_delete AFTER DELETE ON source_event_scope_rows BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_source_events_insert AFTER INSERT ON source_events BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_source_events_update AFTER UPDATE ON source_events BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_source_events_delete AFTER DELETE ON source_events BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_source_generations_insert AFTER INSERT ON source_generations BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_source_generations_update AFTER UPDATE ON source_generations BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_source_generations_delete AFTER DELETE ON source_generations BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_source_units_insert AFTER INSERT ON source_units BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_source_units_update AFTER UPDATE ON source_units BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_source_units_delete AFTER DELETE ON source_units BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_watches_insert AFTER INSERT ON watches BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_watches_update AFTER UPDATE ON watches BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_watches_delete AFTER DELETE ON watches BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_work_attempts_insert AFTER INSERT ON work_attempts BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_work_attempts_update AFTER UPDATE ON work_attempts BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_work_attempts_delete AFTER DELETE ON work_attempts BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_work_generations_insert AFTER INSERT ON work_generations BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_work_generations_update AFTER UPDATE ON work_generations BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_work_generations_delete AFTER DELETE ON work_generations BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER "publication_fence_history_dispatch_fence_insert" BEFORE INSERT ON "history_dispatch_fence" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_history_dispatch_fence_update" BEFORE UPDATE ON "history_dispatch_fence" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_history_dispatch_fence_delete" BEFORE DELETE ON "history_dispatch_fence" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE INDEX finding_support_references_digest ON finding_support_references(kind,sha256);
CREATE TRIGGER "publication_fence_finding_support_references_insert" BEFORE INSERT ON "finding_support_references" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_finding_support_references_update" BEFORE UPDATE ON "finding_support_references" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_finding_support_references_delete" BEFORE DELETE ON "finding_support_references" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER retention_apply_no_delete BEFORE DELETE ON retention_applies
BEGIN SELECT RAISE(ABORT,'retention apply notes are permanent'); END;
CREATE TRIGGER retention_apply_no_rewrite BEFORE UPDATE ON retention_applies
WHEN old.files_completed_at IS NOT NULL
    OR old.receipt_json IS NOT NULL
    OR new.plan_digest <> old.plan_digest
    OR new.planned_files_json <> old.planned_files_json
    OR new.planned_payloads_json <> old.planned_payloads_json
    OR new.removed_payload_bytes <> old.removed_payload_bytes
    OR new.started_at <> old.started_at
BEGIN SELECT RAISE(ABORT,'a retention apply note is written once and finished once'); END;
CREATE TRIGGER "publication_fence_retention_applies_insert" BEFORE INSERT ON "retention_applies" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_retention_applies_update" BEFORE UPDATE ON "retention_applies" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_retention_applies_delete" BEFORE DELETE ON "retention_applies" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE INDEX derivation_row_refs_payload ON derivation_row_refs(payload_sha256);
CREATE TRIGGER derivation_row_ref_no_update BEFORE UPDATE ON derivation_row_refs
BEGIN SELECT RAISE(ABORT,'derivation rows are immutable'); END;
CREATE TRIGGER derivation_payload_no_update BEFORE UPDATE ON derivation_payloads
BEGIN SELECT RAISE(ABORT,'derivation payloads are immutable'); END;
CREATE VIEW derivation_rows(generation_id,ordinal,table_name,record_key,payload_json) AS
SELECT r.generation_id,r.ordinal,r.table_name,r.record_key,p.payload_json
FROM derivation_row_refs r JOIN derivation_payloads p ON p.payload_sha256=r.payload_sha256;
CREATE TRIGGER history_timing_derivation_row_refs_insert AFTER INSERT ON derivation_row_refs BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_derivation_row_refs_update AFTER UPDATE ON derivation_row_refs BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_derivation_row_refs_delete AFTER DELETE ON derivation_row_refs BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_derivation_payloads_insert AFTER INSERT ON derivation_payloads BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_derivation_payloads_update AFTER UPDATE ON derivation_payloads BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_derivation_payloads_delete AFTER DELETE ON derivation_payloads BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER "publication_fence_derivation_payloads_insert" BEFORE INSERT ON "derivation_payloads" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_derivation_payloads_update" BEFORE UPDATE ON "derivation_payloads" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_derivation_row_refs_insert" BEFORE INSERT ON "derivation_row_refs" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_derivation_row_refs_update" BEFORE UPDATE ON "derivation_row_refs" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_narrow_fence_derivation_row_refs_insert" BEFORE INSERT ON "derivation_row_refs" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind='publication_narrow') AND (EXISTS(SELECT 1 FROM derivation_generations g WHERE g.generation_id=NEW.generation_id AND g.stage='build') AND NOT EXISTS(SELECT 1 FROM "derivation_row_refs" k WHERE k."generation_id"=NEW."generation_id" AND k."ordinal"=NEW."ordinal")) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_narrow_fence_derivation_row_refs_update" BEFORE UPDATE ON "derivation_row_refs" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind='publication_narrow') AND ((EXISTS(SELECT 1 FROM derivation_generations g WHERE g.generation_id=OLD.generation_id AND g.stage='build') OR EXISTS(SELECT 1 FROM derivation_generations g WHERE g.generation_id=NEW.generation_id AND g.stage='build'))) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_derivation_payload_removal_grant_insert" BEFORE INSERT ON "derivation_payload_removal_grant" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_derivation_payload_removal_grant_update" BEFORE UPDATE ON "derivation_payload_removal_grant" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_derivation_payload_removal_grant_delete" BEFORE DELETE ON "derivation_payload_removal_grant" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_derivation_payload_removal_authority_insert" BEFORE INSERT ON "derivation_payload_removal_authority" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_derivation_payload_removal_authority_update" BEFORE UPDATE ON "derivation_payload_removal_authority" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_derivation_payload_removal_authority_delete" BEFORE DELETE ON "derivation_payload_removal_authority" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE INDEX derivation_equivalences_generation ON derivation_equivalences(generation_id);
CREATE INDEX derivation_equivalences_dependencies ON derivation_equivalences(dependency_set_id);
CREATE INDEX derivation_equivalences_run ON derivation_equivalences(run_id);
CREATE INDEX derivation_equivalences_recipe ON derivation_equivalences(recipe_sha256);
CREATE INDEX source_equivalences_generation ON source_equivalences(generation_id);
CREATE INDEX source_equivalences_run ON source_equivalences(run_id);
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
CREATE TRIGGER derivation_source_units_update AFTER UPDATE ON source_units
WHEN OLD.watch_id IS NOT NEW.watch_id OR OLD.unit_key IS NOT NEW.unit_key
    OR OLD.accepted_generation_id IS NOT NEW.accepted_generation_id
    OR OLD.legacy_snapshot_id IS NOT NEW.legacy_snapshot_id
BEGIN INSERT INTO derivation_input_versions VALUES ('watch',OLD.watch_id,1) ON CONFLICT(namespace,input_key) DO UPDATE SET version=version+1; INSERT INTO derivation_input_versions VALUES ('watch',NEW.watch_id,1) ON CONFLICT(namespace,input_key) DO UPDATE SET version=version+1; END;
CREATE TRIGGER derivation_source_selections_update AFTER UPDATE ON source_units
WHEN OLD.watch_id IS NOT NEW.watch_id OR OLD.unit_key IS NOT NEW.unit_key
    OR OLD.accepted_generation_id IS NOT NEW.accepted_generation_id
    OR OLD.legacy_snapshot_id IS NOT NEW.legacy_snapshot_id
BEGIN INSERT INTO derivation_input_versions VALUES ('source_selections','all',1) ON CONFLICT(namespace,input_key) DO UPDATE SET version=version+1; END;
CREATE TRIGGER "publication_fence_derivation_recipes_insert" BEFORE INSERT ON "derivation_recipes" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_derivation_recipes_update" BEFORE UPDATE ON "derivation_recipes" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_derivation_recipes_delete" BEFORE DELETE ON "derivation_recipes" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_derivation_equivalences_insert" BEFORE INSERT ON "derivation_equivalences" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_derivation_equivalences_update" BEFORE UPDATE ON "derivation_equivalences" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_derivation_equivalences_delete" BEFORE DELETE ON "derivation_equivalences" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_source_equivalences_insert" BEFORE INSERT ON "source_equivalences" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_source_equivalences_update" BEFORE UPDATE ON "source_equivalences" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_source_equivalences_delete" BEFORE DELETE ON "source_equivalences" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE INDEX runs_loop_open ON runs(loop, finished_at);
CREATE INDEX work_queue_due ON work_queue(state, next_attempt_at);
CREATE INDEX acquisition_plan_eligible ON acquisition_plan(kind, reason, rank);
CREATE INDEX guard_reasons_expiry ON guard_reasons(expires_at);
CREATE INDEX derivation_building_unit ON derivation_building(stage,unit_kind,unit_id);
CREATE TRIGGER "publication_fence_work_queue_insert" BEFORE INSERT ON "work_queue" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_work_queue_update" BEFORE UPDATE ON "work_queue" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_work_queue_delete" BEFORE DELETE ON "work_queue" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_acquisition_plan_insert" BEFORE INSERT ON "acquisition_plan" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_acquisition_plan_update" BEFORE UPDATE ON "acquisition_plan" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_acquisition_plan_delete" BEFORE DELETE ON "acquisition_plan" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_guard_reasons_insert" BEFORE INSERT ON "guard_reasons" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_guard_reasons_update" BEFORE UPDATE ON "guard_reasons" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_guard_reasons_delete" BEFORE DELETE ON "guard_reasons" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_loop_state_insert" BEFORE INSERT ON "loop_state" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_loop_state_update" BEFORE UPDATE ON "loop_state" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_loop_state_delete" BEFORE DELETE ON "loop_state" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_derivation_building_insert" BEFORE INSERT ON "derivation_building" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_derivation_building_update" BEFORE UPDATE ON "derivation_building" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_derivation_building_delete" BEFORE DELETE ON "derivation_building" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE INDEX derivation_reads_key ON derivation_reads(read_key);
CREATE INDEX source_event_proposals_event ON source_event_proposals(event_id);
CREATE TRIGGER "publication_fence_derivation_reads_insert" BEFORE INSERT ON "derivation_reads" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_derivation_reads_update" BEFORE UPDATE ON "derivation_reads" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_derivation_reads_delete" BEFORE DELETE ON "derivation_reads" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_source_event_proposals_insert" BEFORE INSERT ON "source_event_proposals" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_source_event_proposals_update" BEFORE UPDATE ON "source_event_proposals" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_source_event_proposals_delete" BEFORE DELETE ON "source_event_proposals" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_release_partitions_insert" BEFORE INSERT ON "release_partitions" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_release_partitions_update" BEFORE UPDATE ON "release_partitions" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_release_partitions_delete" BEFORE DELETE ON "release_partitions" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_narrow_fence_release_partitions_insert" BEFORE INSERT ON "release_partitions" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind='publication_narrow') AND (NOT EXISTS(SELECT 1 FROM "release_partitions" k WHERE k."partition"=NEW."partition")) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_narrow_fence_release_partitions_update" BEFORE UPDATE ON "release_partitions" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind='publication_narrow') AND (1) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_narrow_fence_release_partitions_delete" BEFORE DELETE ON "release_partitions" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind='publication_narrow') AND (1) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_release_artifacts_insert" BEFORE INSERT ON "release_artifacts" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_release_artifacts_update" BEFORE UPDATE ON "release_artifacts" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_release_artifacts_delete" BEFORE DELETE ON "release_artifacts" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_narrow_fence_release_artifacts_insert" BEFORE INSERT ON "release_artifacts" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind='publication_narrow') AND (NOT EXISTS(SELECT 1 FROM "release_artifacts" k WHERE k."artifact_digest"=NEW."artifact_digest")) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_narrow_fence_release_artifacts_update" BEFORE UPDATE ON "release_artifacts" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind='publication_narrow') AND (1) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_narrow_fence_release_artifacts_delete" BEFORE DELETE ON "release_artifacts" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind='publication_narrow') AND (1) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_release_manifests_insert" BEFORE INSERT ON "release_manifests" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_release_manifests_update" BEFORE UPDATE ON "release_manifests" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_release_manifests_delete" BEFORE DELETE ON "release_manifests" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_narrow_fence_release_manifests_insert" BEFORE INSERT ON "release_manifests" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind='publication_narrow') AND (NOT EXISTS(SELECT 1 FROM "release_manifests" k WHERE k."manifest_digest"=NEW."manifest_digest")) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_narrow_fence_release_manifests_update" BEFORE UPDATE ON "release_manifests" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind='publication_narrow') AND (1) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_narrow_fence_release_manifests_delete" BEFORE DELETE ON "release_manifests" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind='publication_narrow') AND (1) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE INDEX source_generation_packs_digest ON source_generation_packs(pack_digest);
CREATE INDEX label_body_packs_digest ON label_body_packs(pack_digest);
CREATE INDEX derivation_generations_output ON derivation_generations(output_digest);
CREATE TRIGGER derivation_row_ref_no_delete BEFORE DELETE ON derivation_row_refs
WHEN NOT EXISTS (SELECT 1 FROM storage_eviction_authority)
BEGIN SELECT RAISE(ABORT,'derivation rows are immutable'); END;
CREATE TRIGGER derivation_dependency_no_delete BEFORE DELETE ON derivation_dependency_sets
WHEN NOT EXISTS (SELECT 1 FROM storage_eviction_authority)
BEGIN SELECT RAISE(ABORT,'derivation dependency manifests are immutable'); END;
CREATE TRIGGER derivation_payload_guarded_delete BEFORE DELETE ON derivation_payloads
WHEN NOT EXISTS (SELECT 1 FROM derivation_payload_removal_authority)
    AND NOT EXISTS (SELECT 1 FROM storage_eviction_authority)
BEGIN SELECT RAISE(ABORT,'derivation payload removal requires authority'); END;
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
CREATE TRIGGER storage_apply_no_delete BEFORE DELETE ON storage_applies
BEGIN SELECT RAISE(ABORT,'storage apply notes are permanent'); END;
CREATE TRIGGER storage_apply_no_rewrite BEFORE UPDATE ON storage_applies
WHEN OLD.finished_at IS NOT NULL
    OR NEW.plan_digest IS NOT OLD.plan_digest OR NEW.kind IS NOT OLD.kind
    OR NEW.started_at IS NOT OLD.started_at
BEGIN SELECT RAISE(ABORT,'a storage apply note is written once and finished once'); END;
CREATE INDEX backup_objects_segment ON backup_objects(transport,segment);
CREATE TRIGGER "publication_fence_source_generations_update" BEFORE UPDATE ON "source_generations" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_narrow_fence_source_generations_update" BEFORE UPDATE ON "source_generations" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind='publication_narrow') AND ((OLD.state='revoked' OR NEW.state='revoked')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_derivation_dependency_sets_delete" BEFORE DELETE ON "derivation_dependency_sets" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_derivation_generations_update" BEFORE UPDATE ON "derivation_generations" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_narrow_fence_derivation_generations_update" BEFORE UPDATE ON "derivation_generations" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind='publication_narrow') AND ((OLD.stage='build' OR NEW.stage='build')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_derivation_payloads_delete" BEFORE DELETE ON "derivation_payloads" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_narrow_fence_derivation_payloads_delete" BEFORE DELETE ON "derivation_payloads" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind='publication_narrow') AND (1) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_derivation_row_refs_delete" BEFORE DELETE ON "derivation_row_refs" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_narrow_fence_derivation_row_refs_delete" BEFORE DELETE ON "derivation_row_refs" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind='publication_narrow') AND (EXISTS(SELECT 1 FROM derivation_generations g WHERE g.generation_id=OLD.generation_id AND g.stage='build')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_blob_classes_insert" BEFORE INSERT ON "blob_classes" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_blob_classes_update" BEFORE UPDATE ON "blob_classes" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_blob_classes_delete" BEFORE DELETE ON "blob_classes" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_objects_insert" BEFORE INSERT ON "objects" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_objects_update" BEFORE UPDATE ON "objects" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_objects_delete" BEFORE DELETE ON "objects" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_narrow_fence_objects_insert" BEFORE INSERT ON "objects" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind='publication_narrow') AND ((NEW.class IN ('release_manifest','label_body') OR (NEW.class='derivation_output' AND EXISTS(SELECT 1 FROM derivation_generations g WHERE g.output_digest=NEW.digest AND g.stage='build'))) AND NOT EXISTS(SELECT 1 FROM "objects" k WHERE k."class"=NEW."class" AND k."digest"=NEW."digest")) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_narrow_fence_objects_update" BEFORE UPDATE ON "objects" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind='publication_narrow') AND (((OLD.class IN ('release_manifest','label_body') OR (OLD.class='derivation_output' AND EXISTS(SELECT 1 FROM derivation_generations g WHERE g.output_digest=OLD.digest AND g.stage='build'))) OR (NEW.class IN ('release_manifest','label_body') OR (NEW.class='derivation_output' AND EXISTS(SELECT 1 FROM derivation_generations g WHERE g.output_digest=NEW.digest AND g.stage='build'))))) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_narrow_fence_objects_delete" BEFORE DELETE ON "objects" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind='publication_narrow') AND ((OLD.class IN ('release_manifest','label_body') OR (OLD.class='derivation_output' AND EXISTS(SELECT 1 FROM derivation_generations g WHERE g.output_digest=OLD.digest AND g.stage='build')))) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_derivation_evicted_insert" BEFORE INSERT ON "derivation_evicted" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_derivation_evicted_update" BEFORE UPDATE ON "derivation_evicted" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_derivation_evicted_delete" BEFORE DELETE ON "derivation_evicted" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_narrow_fence_derivation_evicted_insert" BEFORE INSERT ON "derivation_evicted" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind='publication_narrow') AND (EXISTS(SELECT 1 FROM derivation_generations g WHERE g.generation_id=NEW.generation_id AND g.stage='build') AND NOT EXISTS(SELECT 1 FROM "derivation_evicted" k WHERE k."generation_id"=NEW."generation_id")) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_narrow_fence_derivation_evicted_update" BEFORE UPDATE ON "derivation_evicted" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind='publication_narrow') AND ((EXISTS(SELECT 1 FROM derivation_generations g WHERE g.generation_id=OLD.generation_id AND g.stage='build') OR EXISTS(SELECT 1 FROM derivation_generations g WHERE g.generation_id=NEW.generation_id AND g.stage='build'))) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_narrow_fence_derivation_evicted_delete" BEFORE DELETE ON "derivation_evicted" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind='publication_narrow') AND (EXISTS(SELECT 1 FROM derivation_generations g WHERE g.generation_id=OLD.generation_id AND g.stage='build')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_source_generation_packs_insert" BEFORE INSERT ON "source_generation_packs" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_source_generation_packs_update" BEFORE UPDATE ON "source_generation_packs" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_source_generation_packs_delete" BEFORE DELETE ON "source_generation_packs" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_narrow_fence_source_generation_packs_insert" BEFORE INSERT ON "source_generation_packs" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind='publication_narrow') AND (EXISTS(SELECT 1 FROM source_generations s WHERE s.generation_id=NEW.generation_id AND s.state='revoked') AND NOT EXISTS(SELECT 1 FROM "source_generation_packs" k WHERE k."generation_id"=NEW."generation_id")) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_narrow_fence_source_generation_packs_update" BEFORE UPDATE ON "source_generation_packs" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind='publication_narrow') AND ((EXISTS(SELECT 1 FROM source_generations s WHERE s.generation_id=OLD.generation_id AND s.state='revoked') OR EXISTS(SELECT 1 FROM source_generations s WHERE s.generation_id=NEW.generation_id AND s.state='revoked'))) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_narrow_fence_source_generation_packs_delete" BEFORE DELETE ON "source_generation_packs" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind='publication_narrow') AND (EXISTS(SELECT 1 FROM source_generations s WHERE s.generation_id=OLD.generation_id AND s.state='revoked')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_label_body_packs_insert" BEFORE INSERT ON "label_body_packs" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_label_body_packs_update" BEFORE UPDATE ON "label_body_packs" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_label_body_packs_delete" BEFORE DELETE ON "label_body_packs" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_narrow_fence_label_body_packs_insert" BEFORE INSERT ON "label_body_packs" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind='publication_narrow') AND (EXISTS(SELECT 1 FROM derivation_generations g WHERE g.generation_id=NEW.generation_id AND g.stage='build') AND NOT EXISTS(SELECT 1 FROM "label_body_packs" k WHERE k."generation_id"=NEW."generation_id")) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_narrow_fence_label_body_packs_update" BEFORE UPDATE ON "label_body_packs" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind='publication_narrow') AND ((EXISTS(SELECT 1 FROM derivation_generations g WHERE g.generation_id=OLD.generation_id AND g.stage='build') OR EXISTS(SELECT 1 FROM derivation_generations g WHERE g.generation_id=NEW.generation_id AND g.stage='build'))) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_narrow_fence_label_body_packs_delete" BEFORE DELETE ON "label_body_packs" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind='publication_narrow') AND (EXISTS(SELECT 1 FROM derivation_generations g WHERE g.generation_id=OLD.generation_id AND g.stage='build')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_storage_eviction_grant_insert" BEFORE INSERT ON "storage_eviction_grant" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_storage_eviction_grant_update" BEFORE UPDATE ON "storage_eviction_grant" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_storage_eviction_grant_delete" BEFORE DELETE ON "storage_eviction_grant" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_storage_eviction_authority_insert" BEFORE INSERT ON "storage_eviction_authority" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_storage_eviction_authority_update" BEFORE UPDATE ON "storage_eviction_authority" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_storage_eviction_authority_delete" BEFORE DELETE ON "storage_eviction_authority" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_storage_applies_insert" BEFORE INSERT ON "storage_applies" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_storage_applies_update" BEFORE UPDATE ON "storage_applies" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE TRIGGER "publication_fence_storage_applies_delete" BEFORE DELETE ON "storage_applies" WHEN EXISTS(SELECT 1 FROM execution_admissions WHERE state='active' AND action_kind IN ('publish','publication')) BEGIN SELECT RAISE(ABORT,'active publication fences semantic writes'); END;
CREATE INDEX execution_dependencies_scope ON execution_dependencies(scope_kind,scope_id,action_id);
COMMIT;
