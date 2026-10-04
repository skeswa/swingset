# D-0251: A rejected legacy header publishes the baseline's row, and the gate will check result events

Recorded: 2026-10-13  
Decided by: agent, 2026-10-13, under D-0172; source: the release-losses investigation of 2026-10-12  
Topic: Publication  
Supersedes: the D-0224 rule that a rejected legacy `events` row withholds its whole event  
Superseded by: —

## Decision

- When an event's `events` header row has no accepted support and differs
  from the baseline, and no admissible header holds the event, the build
  publishes the baseline's own `events` row in its place. The event's
  accepted results publish with it. Both build paths do this
  (`build/partitions.py`, `build/closure_rows.py`).
- Every column of that row is the baseline's, provenance included
  (`source`, `sources`, `snapshot_id`, `parser_version`, `run_id`,
  `first_seen_at`, `last_seen_at`). Only `coverage_tier` (the year's patch)
  and the freshness columns are recomputed. A revoked baseline snapshot, an
  event the baseline lacks, or a rejected result row still withholds the
  event. A non-baseline legacy row never publishes.
- The gate will refuse a candidate in which an event that had placements or
  entries in the baseline has none (`lost_result_events`); an override row
  `result_event:<event_id>` approves one by name. `publish/gate.py` is in
  every project and link scope, so this half waits for the next change that
  moves those scopes ([maintenance item 28](../../docs/plans/README.md)).

## Why

308 baseline events, 19 with results (2,736 placements, 10,679 entries,
all 9 WDR events), were withheld because newer, unassessed calendar captures
changed their header rows. The baseline row is already public, so
republishing it adds nothing new. The `events 2105->1805` approval was a
count floor and did not show that results were lost; `events_with_placements`
rose, so totals hid them.

## Alternatives and consequences

A calendar admission contract would publish the corrected dates, but it is a
full re-run; it stays the follow-up. Until then 10 result events keep the
baseline's month-wide dates.

## Links

[Release-losses investigation](../investigations/2026/release-losses-2026-10-12.md)
