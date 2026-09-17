-- Conservative dispatch proof invalidation; never grants acquisition authority.
-- Covers candidate, year inventory, selected parents, capture and retry inputs.
-- Timing/scheduler/host-accounting rows are not candidate evidence; consumers
-- separately assess current host, allowance, pressure and control gates.
CREATE TABLE history_dispatch_fence (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1), revision INTEGER NOT NULL
);
INSERT INTO history_dispatch_fence VALUES(1,0);
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
CREATE TRIGGER history_timing_derivation_rows_insert AFTER INSERT ON derivation_rows BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_derivation_rows_update AFTER UPDATE ON derivation_rows BEGIN
    UPDATE history_dispatch_fence SET revision=revision+1 WHERE singleton=1;
END;
CREATE TRIGGER history_timing_derivation_rows_delete AFTER DELETE ON derivation_rows BEGIN
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
