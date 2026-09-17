# Event extension acceptance audit — 2026-09-17

This maps the H14/H16 event-completion scenarios to executable local evidence.
It does not accept the extension, establish production throughput, or publish a
release. The coordinator owns the integrated source freeze, formatting, full
validation, migration rehearsal and operating receipts.

The requirements are in [the recovery plan](../../../docs/plans/recovery/README.md).
The [timing receipt](event-timing-2026-09-17.md) describes the bounded timing
implementation and its limits. Historical test receipts remain evidence for
their recorded bytes; this audit does not combine their counts into a new run.

## Scenario mapping

Test names below are exact. A fixture proves its finite inputs and controlled
interleavings; it does not establish a service objective for live demand.

| Scenario                                                                                              | Executable local evidence                                                                                                                                                                                                                                                                                                                                                                                                                                                                            | Remaining acceptance limit                                                                                                                                                                                                                                                       |
| ----------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| A 33-page admitted event finishes despite new events and current refreshes                            | `tests/test_event_completion_cohort.py::test_admitted_33_page_cohort_finishes_amid_discovery_and_current_refresh` exercises mocked HTTP, acquisition and admission. `tests/test_event_turns.py::test_existing_large_and_older_events_finish_under_continuous_new_arrivals` checks larger finite scheduler populations.                                                                                                                                                                               | The latter seals synthetic watches; neither measures production demand, throughput or elapsed operating time.                                                                                                                                                                    |
| A failed page preserves independent service and does not count as successful progress                 | `tests/test_event_turns.py::test_blocked_owner_keeps_place_while_independent_event_proceeds`; `tests/test_event_progress.py::test_failed_fetch_does_not_record_success`; `tests/test_event_timing.py::test_real_failed_fetch_does_not_reset_success_wait_across_reverified_phase`.                                                                                                                                                                                                                   | Historical dispatch timing and interpretation eligibility remain unknown. A finite failure sequence is not a production observation period.                                                                                                                                      |
| Partial turn survives restart, hold and UTC daily reset without refunds or catch-up burst             | New `tests/test_event_extension_acceptance.py::test_partial_turn_survives_crash_hold_and_daily_reset_without_extra_debits` combines a crash after paid admission, database reopen, all-work pause, next-day reset, retry delay and a sibling request. Also `tests/test_h14_acceptance.py::test_retry_cooldown_and_depleted_quota_resume_without_burst` and `tests/test_event_turns.py::test_restart_and_policy_reload_preserve_partial_turn_and_debits`.                                             | The new test uses finite mocked membership and real accounting/control gates; it does not claim successful body interpretation.                                                                                                                                                  |
| Aliases, archived parents and lost parse hints preserve source grouping and work age                  | `tests/test_event_enumerations.py::test_bootstrap_is_durable_alias_independent_and_does_not_schedule_requests`; `tests/test_event_pressure_probe.py::test_direct_result_and_archive_redirect_identity`; `tests/test_parse_hint_recovery.py::test_retry_deadline_and_original_attempt_token_survive_missing_hint`; `tests/test_parse_hint_recovery.py::test_retained_child_reconstructs_and_parses_under_normal_pause_gate`.                                                                          | These cover the contracts separately. They do not prove every historical source's real archived layout or exact legacy timing.                                                                                                                                                   |
| New pages, unauthorized omissions and incomplete pagination preserve denominators                     | `tests/test_event_enumerations.py::test_additions_and_unauthorized_omissions_preserve_old_denominator_and_wait_age`; `tests/test_event_enumerations.py::test_authority_retires_only_its_own_contribution_and_preserves_shared_requests`; `tests/build/test_event_coverage.py::test_actual_build_binds_witness_and_reuses_only_matching_enumeration`; `tests/build/test_event_coverage.py::test_bounded_capture_exposes_unassessed_instead_of_partial_total`.                                         | Unknown pagination and bounded unassessed populations remain explicit; a smaller observed population does not prove completion.                                                                                                                                                  |
| Expansion pressure preserves listed-page capacity and permits later draining                          | `tests/test_event_pressure.py::test_pressure_keeps_continuations_and_borrowing_but_excludes_new_indexes`; `tests/test_event_pressure.py::test_mapping_and_publication_do_not_reopen_local_pressure`; `tests/test_event_capacity.py::test_weighted_share_under_continuous_discovery_with_event_rotation`; `tests/test_event_capacity.py::test_borrowing_preserves_credit_and_does_not_create_catchup_debt`.                                                                                           | Retained offline pressure-drain evidence also lives in the 2026-09-16 event-completion records. Turn size, share and expansion thresholds still need calibration against a fixed production cohort.                                                                              |
| Redirects, robots, retries and shared pages charge one event owner and every actual host              | `tests/test_event_turns.py::test_shared_watch_redirect_robots_and_retry_have_one_owner_and_actual_hosts`; `tests/test_event_capacity.py::test_shared_redirect_chain_charges_one_lane_and_each_actual_host`; `tests/test_event_timing.py::test_actual_network_wait_credits_other_host_event_and_preserves_one_debit_owner`; `tests/test_event_progress.py::test_shared_request_has_one_operation_and_progress_for_each_event`.                                                                        | Shared acquisition does not imply one canonical event mapping or identical publication support.                                                                                                                                                                                  |
| Doctor distinguishes blocked gates, wall age and eligible waiting without resetting alarms on failure | `tests/test_event_readiness.py::test_overlapping_recorded_blockers_use_archive_host_without_mutation`; `tests/test_event_reporting.py::test_doctor_event_drilldown_keeps_unknowns_and_does_not_mutate`; `tests/test_event_timing.py::test_advancing_clock_cycle_emits_lower_bound_breach_despite_bookkeeping_gaps`; `tests/test_event_timing.py::test_pause_between_gate_assessment_and_open_boundary_cannot_credit_paused_time`.                                                                    | Doctor and daily summary share the report object; exact timing and lower-bound alarm behavior have separate checks. There is no separate integrated human rendering assertion for every new alarm state. Objectives are opt-in, uncalibrated and not yet observed in production. |
| All pages acquired, one unsupported page, unresolved mapping and delayed publication stay distinct    | New `tests/test_event_extension_acceptance.py::test_unsupported_unmapped_coverage_waits_for_exact_acknowledgment` joins actual coverage capture/validation with local mocked publication receipts. `tests/build/test_unsupported_coverage.py::test_explicit_unknown_is_body_backed_gap_not_success`; `tests/test_event_publication.py::test_local_build_never_advances_acknowledged_event_progress`; `tests/test_event_publication.py::test_receipt_for_another_candidate_is_not_an_acknowledgment`. | The combined test is not a full build or remote publication. A subsequent release still needs frozen coverage, independent audit, acknowledged publication and remote verification.                                                                                              |
| Whole-event retirement requires authority and survives restore as history, not current proof          | `tests/test_source_event_retirement.py::test_independent_empty_group_survives_the_original_owners_withdrawal`; `tests/test_source_event_retirement.py::test_exhausted_source_domain_never_proves_absence`; `tests/test_source_event_retirement.py::test_admission_journal_insert_invalidates_a_source_domain_absence_proof`; new `tests/test_event_extension_acceptance.py::test_activated_restore_retains_whole_event_history_but_requires_fresh_proof`.                                            | A complete retained declaration domain must fit the verification bound. Incomplete domains stay unknown. Recorded transitions do not reconstruct unobserved or legacy history.                                                                                                   |

## New checks and independent retirement review

The three new combined tests passed in 0.69 seconds before the final retirement
review. The independent review then ran:

```sh
.venv/bin/pytest -q tests/test_source_event_retirement.py tests/test_event_history.py tests/test_event_retirement.py tests/test_event_extension_acceptance.py
```

Result: **52 passed in 3.88 seconds**. This is a focused local receipt, not a
full-suite or post-format receipt. The later history CLI change to open a
read-only connection without taking the writer lock is a separate source change;
the retirement branch retains its own updated validation receipt.

The review checked authority, independent empty declarations, bounded domain
exhaustion, changed admission support, restore invalidation and history paging.
An admission inserted for an existing generation could escape ordinary source
revision invalidation. The implemented fix binds the absence proof to the
admission journal high-water mark and rechecks it before persistence and reporting.
Any later admission conservatively invalidates the current absence claim.

No blocking finding remains in that reviewed implementation. History uses four
separate bounded streams with pinned high-water marks and explicit page counts.
Enumeration cursors are SQLite row identifiers scoped to the same retained
database, not portable identities across reconstructed databases or arbitrary
maintenance. Normal checkpoint restore retains them; no repository VACUUM or
dump/reconstruction operation was found. Immutable receipts preserve what was
observed; current proof still depends on current evidence and restore epochs.

## Gates still open

The extension needs the coordinator's source-bound integrated checks and an actual
schema-14 production backup/restore and migration rehearsal before deployment.
Production input bindings, holds and ordinary jobs need reconciliation. A fixed
admitted cohort then needs measured demand, service, objectives and elapsed
operating observation, followed by the new audited and acknowledged release.
Neither this audit nor local passing tests closes those gates.

Acquisition timing is bounded and incomplete: historical dispatch, oversized or
unassessed cohorts, interpretation eligibility, unknown gate intervals and legacy
timing are not exact fleet ages. A proven lower-bound breach can alert only with
current eligibility and uninterrupted receipt authority. The complete recorded
history API does not turn sampled observations into complete real-world history.

V5 year acceptance and real-source fixture decisions remain separate owner gates.
H17 still needs independent human labels and defensible accuracy estimates.
H18 activation still waits for V5 and V6, including historical publications, and
its own per-kind recovery and operating evidence. No acceptance is inferred here.

## Later rendering check

The separate parametrized integration test
`tests/test_event_timing_rendering.py::test_real_doctor_json_human_and_summary_preserve_timing_state`
now covers lower-bound alerts, actual operator holds, depleted host budgets and
legacy unknown timing through real CLI JSON, human doctor and summary output.
Four cases passed in 0.89 seconds before coordinator formatting. They compare
the complete event report and verify that the database dump is unchanged.
This closes the rendering assertion gap noted above; it does not replace the
fresh full-suite check or operating evidence.
