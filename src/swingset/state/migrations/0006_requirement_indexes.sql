CREATE INDEX canonical_scope_rows_record_idx ON canonical_scope_rows(table_name,record_key);

-- Support bounded shadow inventory and immutable cohort history at retained scale.
CREATE INDEX snapshots_body_idx ON snapshots(body_sha256,snapshot_id);
CREATE INDEX events_series_month_idx ON events(series_id,event_month);
CREATE INDEX registry_placements_occurrence_idx ON registry_placements(series_id,event_month,event_id);
CREATE INDEX requirement_transitions_requirement_idx ON requirement_transitions(requirement_id,transition_id);
CREATE INDEX requirement_attempts_requirement_idx ON requirement_attempts(requirement_id);
CREATE INDEX placements_leader_entry_idx ON placements(leader_entry_id);
CREATE INDEX placements_follower_entry_idx ON placements(follower_entry_id);
CREATE INDEX identity_links_method_wsdc_idx ON identity_links(method,wsdc_id);
CREATE INDEX watches_kind_watch_idx ON watches(kind,watch_id);
CREATE INDEX finding_support_active_idx ON finding_support(active,finding_id);
CREATE INDEX findings_owner_finding_idx ON findings(owner_kind,finding_id);
-- Schema 5 used a lexical union cursor. Restart its partial scan in the new
-- per-scope natural order; requirement identities, attempts and history survive.
UPDATE requirement_scan SET cursor='',started_at=NULL;
