-- Queues remain parse admission tokens. Derived output has independent identity.
ALTER TABLE snapshots ADD COLUMN extract_recipe_sha256 TEXT;
CREATE TABLE derivation_scopes (
    stage TEXT NOT NULL, unit_kind TEXT NOT NULL, unit_id TEXT NOT NULL,
    desired_fingerprint TEXT, materialized_generation_id TEXT REFERENCES derivation_generations(generation_id),
    registered_at TEXT NOT NULL, PRIMARY KEY(stage,unit_kind,unit_id)
);
CREATE INDEX derivation_scopes_materialized ON derivation_scopes(materialized_generation_id);
CREATE TABLE derivation_dependency_sets (
    dependency_set_id TEXT PRIMARY KEY, manifest_json TEXT NOT NULL
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
CREATE INDEX derivation_generations_scope ON derivation_generations(stage,unit_kind,unit_id,created_at);
CREATE INDEX derivation_generations_dependencies ON derivation_generations(dependency_set_id);
CREATE INDEX derivation_generations_previous ON derivation_generations(previous_generation_id);
CREATE INDEX derivation_generations_run ON derivation_generations(run_id);
CREATE TABLE derivation_rows (
    generation_id TEXT NOT NULL REFERENCES derivation_generations(generation_id) DEFERRABLE INITIALLY DEFERRED,
    ordinal INTEGER NOT NULL, table_name TEXT NOT NULL, record_key TEXT NOT NULL,
    payload_json TEXT NOT NULL, PRIMARY KEY(generation_id,ordinal),
    UNIQUE(generation_id,table_name,record_key)
);
CREATE TRIGGER derivation_generation_no_update BEFORE UPDATE ON derivation_generations
BEGIN SELECT RAISE(ABORT,'derivation generations are immutable'); END;
CREATE TRIGGER derivation_generation_no_delete BEFORE DELETE ON derivation_generations
BEGIN SELECT RAISE(ABORT,'derivation generations are immutable'); END;
CREATE TRIGGER derivation_row_no_update BEFORE UPDATE ON derivation_rows
BEGIN SELECT RAISE(ABORT,'derivation rows are immutable'); END;
CREATE TRIGGER derivation_row_no_delete BEFORE DELETE ON derivation_rows
BEGIN SELECT RAISE(ABORT,'derivation rows are immutable'); END;
CREATE TRIGGER derivation_dependency_no_update BEFORE UPDATE ON derivation_dependency_sets
BEGIN SELECT RAISE(ABORT,'derivation dependency manifests are immutable'); END;
CREATE TRIGGER derivation_dependency_no_delete BEFORE DELETE ON derivation_dependency_sets
BEGIN SELECT RAISE(ABORT,'derivation dependency manifests are immutable'); END;
INSERT INTO derivation_scopes(stage,unit_kind,unit_id,registered_at)
SELECT stage,unit_kind,unit_id,enqueued_at FROM pending_work WHERE stage IN ('project','link');






-- Change tokens are maintained by the same SQLite writes as their inputs. They
-- certify an unchanged exact manifest; they never declare legacy output current.
ALTER TABLE derivation_scopes ADD COLUMN materialized_signature TEXT;
CREATE TABLE derivation_input_versions (
    namespace TEXT NOT NULL, input_key TEXT NOT NULL, version INTEGER NOT NULL,
    PRIMARY KEY(namespace,input_key)
);
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
CREATE TRIGGER derivation_source_units_update AFTER UPDATE ON source_units
BEGIN INSERT INTO derivation_input_versions VALUES ('watch',OLD.watch_id,1) ON CONFLICT(namespace,input_key) DO UPDATE SET version=version+1; INSERT INTO derivation_input_versions VALUES ('watch',NEW.watch_id,1) ON CONFLICT(namespace,input_key) DO UPDATE SET version=version+1; END;
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

INSERT OR IGNORE INTO derivation_scopes(stage,unit_kind,unit_id,registered_at)
SELECT 'project',scope_kind,scope_id,strftime('%Y-%m-%dT%H:%M:%f+00:00','now') FROM observations
WHERE scope_kind IN ('calendar','source_index','dancer','event','source_event') GROUP BY scope_kind,scope_id;
INSERT OR IGNORE INTO derivation_scopes(stage,unit_kind,unit_id,registered_at)
SELECT 'project',scope_kind,scope_id,strftime('%Y-%m-%dT%H:%M:%f+00:00','now') FROM canonical_scope_rows
WHERE scope_kind IN ('calendar','source_index','dancer','event','source_event') GROUP BY scope_kind,scope_id;
INSERT OR IGNORE INTO derivation_scopes(stage,unit_kind,unit_id,registered_at)
SELECT 'project','event',event_id,strftime('%Y-%m-%dT%H:%M:%f+00:00','now') FROM events;
INSERT OR IGNORE INTO derivation_scopes(stage,unit_kind,unit_id,registered_at)
SELECT 'link','event',event_id,strftime('%Y-%m-%dT%H:%M:%f+00:00','now') FROM events;
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
CREATE TRIGGER derivation_source_selections_update AFTER UPDATE ON source_units
BEGIN INSERT INTO derivation_input_versions VALUES ('source_selections','all',1) ON CONFLICT(namespace,input_key) DO UPDATE SET version=version+1; END;
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

-- REPLACE deletes do not run DELETE triggers on every SQLite connection.
-- Preserve invalidation of the old scope before a conflicting insert replaces it.
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
