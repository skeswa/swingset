# H11 acceptance evidence, 2026-09-13

H11's local implementation satisfies its three rollout scenarios and its
operational reporting measures. Schema 9 already contains the deployed shadow
inventory. The schema 10 cohort correction and the final lag-report additions
remain local. This report does not claim deployment, repair activation, or
completion of H12.

## Scenario evidence

| Required H11 behavior                                                                                           | Evidence                                                                                                                                             | Result                                                                                                                                                    |
| --------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Deleted acquisition requirement is recreated from retained evidence                                             | `test_deleted_acquisition_row_is_recreated_from_retained_watch_without_repair`; accepted finding-support recreation and deleted-row transition tests | Same requirement ID returns, without a request or fabricated completion                                                                                   |
| Doctor works while active, idle, blocked, or stopped; human and JSON agree; no writer lock or network is needed | `test_h11_worker_states_and_human_json_use_the_same_local_snapshot`; paused/unknown cohort test; CLI writer-lock test                                | Same consistent snapshot appears in both formats; worker labels state their timestamp basis and do not claim process liveness                             |
| Watch mode refreshes within its configured interval                                                             | `test_doctor_watch_reads_updated_snapshot_after_each_configured_interval`                                                                            | Two snapshots, 0.25 seconds apart on a fake clock, observe an intervening change while the ordinary writer lock remains held                              |
| Fixed cohorts survive retry, satisfaction, reopening, retirement, discovery, and restart                        | `test_cohort_retries_reopening_retirement_discovery_and_restart_balance`; source-retirement and schema migration tests                               | Membership stays fixed; balance equation holds; retry is not progress; reopening reduces completion                                                       |
| New work is separate from baseline membership                                                                   | Same-clock scoped-cohort regression, legacy migration regression, retained schema 10 benchmark                                                       | Existing closed rows and other scopes are excluded; a durable first-open cutoff identifies later discovery; legacy same-time uncertainty remains explicit |
| Unknown, paused, blocked, or incompatible cohorts have no percentage or ETA                                     | Paused/unknown, scope-change, retirement, and retained blocked-cohort tests                                                                          | No fabricated denominator or completion estimate                                                                                                          |
| Historical scan is bounded and restartable                                                                      | Cursor restart and indexed query-plan tests; two retained full-scan benchmarks                                                                       | All retained years participate; at most 100 scopes per page; cursor survives restart                                                                      |

## Operational evidence

The rollout assigns requirement age, time since progress, status age, and
acquisition/derivation lag to H11. Its final review found that explicit pipeline
lag measures were missing. The local report now includes them without adding
repair execution:

- `test_requirement_progress_and_status_age_follow_verified_transitions` checks
  ages with a fake clock and verifies that a failed attempt does not renew
  progress.
- `test_no_progress_alert_uses_clock_and_pause_only_suppresses_eligible_alarm`
  checks the requirement-age alert. Alerts retain evidence, blocking reason,
  last recorded attempt, and next action.
- `test_pipeline_lag_uses_fake_clock_and_keeps_ages_visible_while_paused` checks
  acquisition and queued-derivation lag alerts. No snapshot is acquired and no
  pending work is consumed.

Acquisition lag means time past a recorded watch schedule. Derivation lag means
age of the current legacy queue entry, including parse work. These are labeled
diagnostic measures; host budget eligibility, service-gap objectives, durable
attempt lifecycle, and desired-generation fingerprints belong to H12–H15.
Requirement filters do not silently redefine source-wide watch schedules or
the global legacy queue. Pauses retain ages; acquisition pauses/cooldowns and
the all-scope queue pause suppress their corresponding diagnostics.

The final report was also read from the static schema 10 benchmark copy using
`mode=ro&immutable=1`. Three calls took 1.792, 1.209, and 1.190 seconds over
105,735 requirements, 32,780 queued units, and two cohorts. It showed 4,431
overdue watches and one unknown schedule. The newly captured cohort still had
zero outside discoveries; the synthetic legacy control still had 233 records
with unknown ordering. These are observations on this VM, not universal bounds
or current production queue counts.

The [final machine receipt](verification/h11-final-lag-report-20260913.json)
records a subsequent single report at 1.476 seconds, with 32,780 pending units
and the explicit 1,800-second diagnostic threshold. It pins the report module
hashes and retains the lag summary and cohort counts; no full scan was repeated.

[Earlier retained scan and comparison measurements](h11-retained-inventory-2026-09-13.md)
record the schema 10 migration, full/repeat scan timings, source database hash,
and the original read-only checkpoint-sidecar limitation. Subsequent immutable
checkpoint readers have synthetic WAL-mode regressions and explicit closes.

## Deployment and closure

The remaining actions are operational, with no additional evidence gate:

1. Finish the V4 publication and verified private backup under its schema 9
   runtime, as coordinated by the deployment owner.
2. Keep the existing operator hold effective while activating the reviewed
   schema 10 runtime. Its migration adds only the nullable cohort cutoff;
   existing cohort membership and transition history remain intact. Do not
   open an immutable checkpoint through the migrating runtime.
3. Check doctor in human and JSON modes under the hold: schema 10, shadow mode,
   `execution_enabled=false`, cohort cutoff/legacy uncertainty fields, and
   explicit pipeline lag. A stale scan remains visibly stale until scanned.
4. Confirm restart preserves reporting and cohort metadata. The already-tested
   bounded shadow scan can run without acquisition or repair execution.
5. Record the runtime and migration receipt, then mark H11 complete. Preserve
   the separate H12 gate; H11 does not require a repair loop, new public joins,
   a new precision target, or an extended live observation period.

The rollout explicitly allows early revisions to exercise their own interfaces
without later consumers or the full repair loop. H18 will rerun applicable
scenarios when each repair kind is activated.
