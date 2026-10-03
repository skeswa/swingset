# D-0237: Unit failures leave the review queue, and requirement history goes

Recorded: 2026-10-03  
Decided by: agent, 2026-10-03, under D-0172; source: architecture plan section 8, phase 5 (owner-adopted 2026-09-25)  
Topic: Local state  
Supersedes: the parts of H11 ([state](../../docs/reference/state.md)) and [D-0213](0213-keep-no-bookkeeping-a-committed-run-does-not-need.md) that describe `work_attempt` requirements and `requirement_attempts`  
Superseded by: —

## Decision

A parse, project or link unit writes no finding, no `finding_support` row
and no requirement attempt. Its failure lives in `work_attempts` (outcome,
reason, retry time) and `work_queue` (reason, error, clock, quarantine),
which the quarantine report and `[unit-quarantined]` already read.

Migration 37 closes every open `work_attempt` finding (`out_of_scope`, "unit
failures live in the work queue"), sets its support inactive so the scan
cannot restore it, bumps the `findings` revision once, and drops
`requirement_attempts`, `requirement_transitions`, `requirement_cohorts`,
`requirement_cohort_members` and the two `findings` triggers that wrote
transitions. `swingset cohort`, `--since` and the summary's
`state/reports/requirements-*.json` cursor go with them.

The requirement scan stays: round, mapping, source ID, occurrence, finalist
and artifact requirements, and the restore of lost review findings, are a
data-quality inventory published in `review_queue`.

Published change: the 852 open `work_attempt` rows (849
`needs_implementation`, 3 `retry_wait`) leave `review_queue` on the next
build. `review_queue` is not a gate measure.

## Why

A `work_attempt` requirement repeated the queue row for the same unit. On
the 09-29 copy the four tables held 902,488 attempts, 121,282 transitions,
one cohort of 36,689 members: 142.6 MB with indexes, written on every unit
run. Only `doctor`'s change counts and a re-created finding's opening time
read them. A requirement re-created after its row was lost now opens at its
creation time; no writer leaves a requirement's opening time in
`finding_support`, so nothing else could keep it.

## Alternatives and consequences

Keeping unit failures in `review_queue` would keep publishing operator
state that a data reviewer cannot act on; the operator reads the quarantine
report. Dropping the tables frees pages, not file size, until a `VACUUM`.

## Links

- [Architecture plan, phase 5](../../docs/plans/architecture.md)
