-- Index every remaining foreign-key child lookup used during scope replacement.
-- Parent deletes must not scan entire retained mark, entry, or evidence tables.
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
