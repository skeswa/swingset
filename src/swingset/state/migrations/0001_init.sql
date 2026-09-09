CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE runs (run_id TEXT PRIMARY KEY, started_at TEXT NOT NULL, finished_at TEXT, dry_run INTEGER NOT NULL CHECK(dry_run IN (0,1)), summary_json TEXT);
CREATE TABLE hosts (host TEXT PRIMARY KEY, next_allowed_at TEXT, paused_until TEXT, pause_reason TEXT, pause_streak INTEGER NOT NULL DEFAULT 0, robots_sha256 TEXT, robots_fetched_at TEXT, robots_status INTEGER);
CREATE TABLE operator_pauses (scope_kind TEXT NOT NULL, scope_id TEXT NOT NULL, until_at TEXT, reason TEXT NOT NULL, PRIMARY KEY(scope_kind, scope_id));
CREATE TABLE host_budget (host TEXT NOT NULL, day TEXT NOT NULL, requests INTEGER NOT NULL DEFAULT 0, bytes INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(host, day));
CREATE TABLE cursors (name TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE watches (
 watch_id TEXT PRIMARY KEY, source TEXT NOT NULL, kind TEXT NOT NULL, method TEXT NOT NULL, url TEXT NOT NULL,
 form TEXT, archive_url TEXT, parser TEXT NOT NULL, source_ref TEXT, state TEXT NOT NULL, next_check_at TEXT,
 last_checked_at TEXT, last_changed_at TEXT, unchanged_streak INTEGER NOT NULL DEFAULT 0, etag TEXT, last_modified TEXT,
 body_sha256 TEXT, paused_until TEXT, notes TEXT, fingerprint TEXT, extract_version TEXT, priority INTEGER NOT NULL DEFAULT 0,
 created_by_snapshot_id TEXT, parent_watch_id TEXT REFERENCES watches(watch_id), ever_ok INTEGER NOT NULL DEFAULT 0,
 current_observation_snapshot_id TEXT, consecutive_404s INTEGER NOT NULL DEFAULT 0, first_404_at TEXT
);
CREATE INDEX watches_due_idx ON watches(state, priority DESC, next_check_at);
CREATE TABLE snapshots (
 snapshot_id TEXT PRIMARY KEY, watch_id TEXT NOT NULL REFERENCES watches(watch_id), method TEXT NOT NULL, url TEXT NOT NULL,
 form TEXT, fetched_at TEXT NOT NULL, http_status INTEGER NOT NULL, etag TEXT, last_modified TEXT, content_type TEXT,
 body_sha256 TEXT, body_bytes INTEGER NOT NULL, content_changed INTEGER NOT NULL CHECK(content_changed IN (0,1)), run_id TEXT NOT NULL REFERENCES runs(run_id),
 via TEXT NOT NULL DEFAULT 'origin', headers_json TEXT NOT NULL DEFAULT '{}', classification TEXT NOT NULL,
 extract_status TEXT, extract_sha256 TEXT, parse_status TEXT, parsed_at TEXT, extract_version TEXT, parser_version TEXT
);
CREATE INDEX snapshots_watch_idx ON snapshots(watch_id, fetched_at, snapshot_id);
CREATE INDEX snapshots_parser_idx ON snapshots(parser_version, extract_version);
CREATE TABLE observations (
 observation_id TEXT PRIMARY KEY, watch_id TEXT NOT NULL REFERENCES watches(watch_id), snapshot_id TEXT NOT NULL REFERENCES snapshots(snapshot_id),
 kind TEXT NOT NULL, scope_kind TEXT NOT NULL, scope_id TEXT NOT NULL, seq INTEGER NOT NULL,
 extract_version TEXT NOT NULL, parser_version TEXT NOT NULL, payload_json TEXT NOT NULL,
 UNIQUE(watch_id, snapshot_id, kind, seq)
);
CREATE INDEX observations_watch_idx ON observations(watch_id);
CREATE INDEX observations_scope_idx ON observations(scope_kind, scope_id);
CREATE TABLE source_event_map (source TEXT NOT NULL, source_ref TEXT NOT NULL, event_id TEXT NOT NULL, match_method TEXT NOT NULL, match_confidence REAL NOT NULL, PRIMARY KEY(source, source_ref));
CREATE INDEX source_event_map_event_idx ON source_event_map(event_id);
CREATE TABLE revisions (name TEXT PRIMARY KEY, value INTEGER NOT NULL DEFAULT 0);
INSERT INTO revisions(name) VALUES ('observations'),('source_event_map'),('source_events'),('canonical'),('dancers'),('links'),('findings'),('snapshots');
CREATE TABLE pending_work (stage TEXT NOT NULL, unit_kind TEXT NOT NULL, unit_id TEXT NOT NULL, enqueued_at TEXT NOT NULL, PRIMARY KEY(stage, unit_kind, unit_id));
CREATE INDEX pending_work_order_idx ON pending_work(stage, enqueued_at, unit_kind, unit_id);
CREATE TABLE accepted_inputs (consumer TEXT NOT NULL, input_name TEXT NOT NULL, digest TEXT NOT NULL, PRIMARY KEY(consumer, input_name));
CREATE TABLE findings (
 finding_id TEXT PRIMARY KEY, owner_kind TEXT NOT NULL, owner_id TEXT NOT NULL, kind TEXT NOT NULL, subject_kind TEXT NOT NULL,
 subject_id TEXT NOT NULL, watch_id TEXT REFERENCES watches(watch_id), snapshot_id TEXT REFERENCES snapshots(snapshot_id), severity TEXT NOT NULL,
 summary TEXT NOT NULL, evidence_json TEXT NOT NULL, suggested_override TEXT, opened_at TEXT NOT NULL, run_id TEXT NOT NULL REFERENCES runs(run_id),
 closed_at TEXT, closed_by TEXT
);
CREATE INDEX findings_owner_idx ON findings(owner_kind, owner_id, closed_at);
CREATE TABLE source_events (
 source TEXT NOT NULL, source_ref TEXT NOT NULL, name_raw TEXT, start_date TEXT, end_date TEXT, location_raw TEXT, url TEXT NOT NULL,
 snapshot_id TEXT NOT NULL, parser_version TEXT NOT NULL, first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, run_id TEXT NOT NULL,
 PRIMARY KEY(source, source_ref)
);
CREATE TABLE source_event_scope_rows (scope_id TEXT NOT NULL, source TEXT NOT NULL, source_ref TEXT NOT NULL, PRIMARY KEY(scope_id, source, source_ref));
CREATE TABLE backup_uploads (path TEXT PRIMARY KEY, sha256 TEXT NOT NULL, uploaded_at TEXT NOT NULL);
CREATE TABLE canonical_scope_rows (scope_kind TEXT NOT NULL, scope_id TEXT NOT NULL, table_name TEXT NOT NULL, record_key TEXT NOT NULL, PRIMARY KEY(scope_kind, scope_id, table_name, record_key));

CREATE TABLE events (event_id TEXT PRIMARY KEY, series_id TEXT NOT NULL, name TEXT NOT NULL, year INTEGER NOT NULL, start_date TEXT NOT NULL, end_date TEXT NOT NULL, city TEXT, region TEXT, country TEXT, website TEXT, wsdc_status TEXT NOT NULL, sources TEXT NOT NULL, live_window_start TEXT, live_window_end TEXT, source TEXT NOT NULL, snapshot_id TEXT NOT NULL, parser_version TEXT NOT NULL, first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, run_id TEXT NOT NULL);
CREATE TABLE contests (contest_id TEXT PRIMARY KEY, event_id TEXT NOT NULL REFERENCES events(event_id) ON DELETE CASCADE, name_raw TEXT NOT NULL, division TEXT NOT NULL, age_division TEXT NOT NULL, contest_type TEXT NOT NULL, partner_mode TEXT NOT NULL, dance_style TEXT NOT NULL, wsdc_points_eligible INTEGER NOT NULL, combined_from TEXT NOT NULL, parse_status TEXT NOT NULL, source_contest_ref TEXT NOT NULL, source TEXT NOT NULL, snapshot_id TEXT NOT NULL, parser_version TEXT NOT NULL, first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, run_id TEXT NOT NULL);
CREATE INDEX contests_event_idx ON contests(event_id);
CREATE TABLE rounds (round_id TEXT PRIMARY KEY, contest_id TEXT NOT NULL REFERENCES contests(contest_id) ON DELETE CASCADE, round_type TEXT NOT NULL, round_index INTEGER NOT NULL, name_raw TEXT NOT NULL, scoring_method TEXT NOT NULL, callback_legend TEXT NOT NULL, judge_count INTEGER NOT NULL, chief_judge_id TEXT, entry_count INTEGER NOT NULL, promoted_count INTEGER, source_round_ref TEXT NOT NULL, score_sheet_url TEXT, source TEXT NOT NULL, snapshot_id TEXT NOT NULL, parser_version TEXT NOT NULL, first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, run_id TEXT NOT NULL);
CREATE INDEX rounds_contest_idx ON rounds(contest_id);
CREATE TABLE entries (entry_id TEXT PRIMARY KEY, contest_id TEXT NOT NULL REFERENCES contests(contest_id) ON DELETE CASCADE, event_id TEXT NOT NULL REFERENCES events(event_id) ON DELETE CASCADE, role TEXT NOT NULL, bib TEXT, name_raw TEXT, name_norm TEXT, partner_name_raw TEXT, city_raw TEXT, country_raw TEXT, wsdc_id INTEGER, link_status TEXT NOT NULL DEFAULT 'unmatched', link_confidence REAL NOT NULL DEFAULT 0, partner_entry_id TEXT, rounds_danced TEXT NOT NULL, best_round TEXT, source TEXT NOT NULL, snapshot_id TEXT NOT NULL, parser_version TEXT NOT NULL, first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, run_id TEXT NOT NULL);
CREATE INDEX entries_event_idx ON entries(event_id);
CREATE TABLE judges (judge_id TEXT PRIMARY KEY, event_id TEXT NOT NULL REFERENCES events(event_id) ON DELETE CASCADE, name_raw TEXT, initials TEXT, anonymous INTEGER NOT NULL, wsdc_id INTEGER, source TEXT NOT NULL, snapshot_id TEXT NOT NULL, parser_version TEXT NOT NULL, first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, run_id TEXT NOT NULL);
CREATE INDEX judges_event_idx ON judges(event_id);
CREATE TABLE heats (heat_id TEXT NOT NULL, round_id TEXT NOT NULL REFERENCES rounds(round_id) ON DELETE CASCADE, heat_number INTEGER NOT NULL, entry_id TEXT NOT NULL REFERENCES entries(entry_id) ON DELETE CASCADE, position INTEGER, source TEXT NOT NULL, snapshot_id TEXT NOT NULL, parser_version TEXT NOT NULL, first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, run_id TEXT NOT NULL, PRIMARY KEY(heat_id, entry_id));
CREATE TABLE callback_marks (round_id TEXT NOT NULL REFERENCES rounds(round_id) ON DELETE CASCADE, entry_id TEXT NOT NULL REFERENCES entries(entry_id) ON DELETE CASCADE, judge_id TEXT NOT NULL REFERENCES judges(judge_id) ON DELETE CASCADE, mark TEXT NOT NULL, mark_raw TEXT NOT NULL, mark_value REAL NOT NULL, source TEXT NOT NULL, snapshot_id TEXT NOT NULL, parser_version TEXT NOT NULL, first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, run_id TEXT NOT NULL, PRIMARY KEY(round_id, entry_id, judge_id));
CREATE TABLE callbacks (round_id TEXT NOT NULL REFERENCES rounds(round_id) ON DELETE CASCADE, entry_id TEXT NOT NULL REFERENCES entries(entry_id) ON DELETE CASCADE, score_sum REAL NOT NULL, yes_count INTEGER NOT NULL, alt_count INTEGER NOT NULL, no_count INTEGER NOT NULL, outcome TEXT NOT NULL, tie_break_applied INTEGER, heat_number INTEGER, source TEXT NOT NULL, snapshot_id TEXT NOT NULL, parser_version TEXT NOT NULL, first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, run_id TEXT NOT NULL, PRIMARY KEY(round_id, entry_id));
CREATE TABLE placements (placement_id TEXT PRIMARY KEY, round_id TEXT NOT NULL REFERENCES rounds(round_id) ON DELETE CASCADE, contest_id TEXT NOT NULL REFERENCES contests(contest_id) ON DELETE CASCADE, event_id TEXT NOT NULL REFERENCES events(event_id) ON DELETE CASCADE, place INTEGER NOT NULL, leader_entry_id TEXT, follower_entry_id TEXT, couple_entry_id TEXT, leader_wsdc_id INTEGER, follower_wsdc_id INTEGER, marks_sorted TEXT, tally TEXT NOT NULL, registry_points_leader INTEGER, registry_points_follower INTEGER, registry_confirmed INTEGER NOT NULL DEFAULT 0, points_matches_expected INTEGER, source TEXT NOT NULL, snapshot_id TEXT NOT NULL, parser_version TEXT NOT NULL, first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, run_id TEXT NOT NULL);
CREATE TABLE final_marks (round_id TEXT NOT NULL REFERENCES rounds(round_id) ON DELETE CASCADE, placement_id TEXT NOT NULL REFERENCES placements(placement_id) ON DELETE CASCADE, judge_id TEXT NOT NULL REFERENCES judges(judge_id) ON DELETE CASCADE, rank INTEGER NOT NULL, source TEXT NOT NULL, snapshot_id TEXT NOT NULL, parser_version TEXT NOT NULL, first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, run_id TEXT NOT NULL, PRIMARY KEY(round_id, placement_id, judge_id));
CREATE TABLE dancers (wsdc_id INTEGER PRIMARY KEY, first_name TEXT NOT NULL, last_name TEXT NOT NULL, name_norm TEXT NOT NULL, is_pro INTEGER NOT NULL, primary_role TEXT NOT NULL, leader_required_level TEXT NOT NULL, leader_allowed_level TEXT NOT NULL, follower_required_level TEXT NOT NULL, follower_allowed_level TEXT NOT NULL, leader_highest_level TEXT NOT NULL, leader_highest_points INTEGER NOT NULL, follower_highest_level TEXT NOT NULL, follower_highest_points INTEGER NOT NULL, recent_year INTEGER NOT NULL, registry_internal_id INTEGER NOT NULL, registry_fetched_at TEXT NOT NULL, merged_into_wsdc_id INTEGER, source TEXT NOT NULL, snapshot_id TEXT NOT NULL, parser_version TEXT NOT NULL, first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, run_id TEXT NOT NULL);
CREATE TABLE registry_placements (wsdc_id INTEGER NOT NULL REFERENCES dancers(wsdc_id) ON DELETE CASCADE, role TEXT NOT NULL, dance_style TEXT NOT NULL, division TEXT NOT NULL, series_id TEXT NOT NULL, series_name_raw TEXT NOT NULL, event_month TEXT NOT NULL, event_id TEXT REFERENCES events(event_id), result TEXT NOT NULL, points INTEGER NOT NULL, source TEXT NOT NULL, snapshot_id TEXT NOT NULL, parser_version TEXT NOT NULL, first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, run_id TEXT NOT NULL, PRIMARY KEY(wsdc_id, role, series_id, event_month, division, dance_style));
CREATE TABLE identity_links (link_id TEXT PRIMARY KEY, subject_kind TEXT NOT NULL, subject_id TEXT NOT NULL, wsdc_id INTEGER, method TEXT NOT NULL, status TEXT NOT NULL, confidence REAL NOT NULL, constraints_applied TEXT NOT NULL, asserted_at TEXT NOT NULL, run_id TEXT NOT NULL, linker_version TEXT NOT NULL, UNIQUE(subject_kind, subject_id));
CREATE TABLE link_candidates (subject_kind TEXT NOT NULL, subject_id TEXT NOT NULL, wsdc_id INTEGER NOT NULL, score REAL NOT NULL, name_similarity REAL NOT NULL, name_rarity REAL NOT NULL, division_ok INTEGER, role_ok INTEGER NOT NULL, recency_ok INTEGER NOT NULL, geography REAL, bib_reuse INTEGER NOT NULL, registry_confirms INTEGER NOT NULL, source_id_confirms INTEGER NOT NULL, rank INTEGER NOT NULL, chosen INTEGER NOT NULL, run_id TEXT NOT NULL, PRIMARY KEY(subject_kind, subject_id, wsdc_id));

INSERT INTO meta(key, value) VALUES ('schema_version', '1'), ('installed_at', strftime('%Y-%m-%dT%H:%M:%fZ','now'));
