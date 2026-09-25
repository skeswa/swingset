# D-0204: Turn off the event bookkeeping stages and open the pressure gate

Recorded: 2026-09-25  
Decided by: agent, 2026-09-25, under D-0175; source: phase 0 of the [architecture plan](../../docs/plans/architecture.md)  
Topic: Scheduling  
Supersedes: in production, the event-index gate of [D-0018](0018-gate-expansion-with-conservative-observations.md)  
Superseded by: —

## Decision

`config/sources.toml` sets all four `[stages]` keys to `false`:
`event_site_review`, `event_pressure`, `event_progress_refresh` and
`event_blocker_observations`. The cycle skips the event-site review, pressure
bootstrap and refresh, the progress refresh and blocker observations. With
pressure off, its gate opens: no host's `event_index` watches are deferred by
pressure, and starts, request debits and event turns write no pressure rows.
The migration 0019 triggers still mark a changed watch or snapshot dirty, at
most one row per watch, and a re-bootstrap drains them. With the progress
refresh off, a fetched or parsed snapshot still appends an
`event_stage_operations` row (`fetch/client.py`, `admission/select.py`).
Nothing the four stages write then gates acquisition.

The `event_*` code, tables and stored rows stay; whether to remove them is an
open owner decision. [D-0201](0201-a-failed-bookkeeping-stage-never-aborts-a-cycle.md)
containment still covers the stages that run. The code default stays `true`,
so other configs keep D-0018.

## Why

Optional bookkeeping has stopped collection twice: D-0201 (the site review
killed every cycle before acquisition) and pressure, which is an acquisition
gate. Pressure counts a subject as unresolved once its observation is over
24 hours old, and dirty rows close the gate on every host. Turning off only
the refresh would close expansion within a day. The progress refresh,
blocker observations and site review feed reports and review findings, not
acquisition; all seventeen years are already accepted.

## Alternatives and consequences

Keeping pressure as a gate bounds how many started events a busy host carries.
Without it, the per-host turn fairness and politeness limits are the only
bound on new `event_index` watches. Reports show pressure as `disabled`, and
progress and blocker rows go stale until a stage is turned back on. The site
review's findings in the published review queue and card stay at their last
values, and their body references keep those blobs. Turning pressure back
on, by config or by a rollback to 019, needs no command: the cycle's
bootstrap drains the dirty rows and enrolls every watch that stored a page.
A start whose request stored no page stays uncounted until that watch is
requested again, so the gate undercounts those until then
([stage switches](../../docs/reference/scheduling.md#stage-switches)). The
dirty-row triggers and the `event_stage_operations` writers stay in the
phase 5 dependency report.

## Links

- [Scheduling reference, stage switches](../../docs/reference/scheduling.md#stage-switches)
