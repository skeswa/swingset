# D-0019: Retain observed changes in event blockers

Recorded: 2026-09-16  
Decided by: agent  
Topic: Event reporting  
Supersedes: —  
Superseded by: —

## Decision

Retain change-only observations of the blocker facts already exposed by event
diagnostics. Rotate a bounded observer through source events in serialized cycle
bookkeeping, under writer ownership and ordinary database transactions. Persist its cursor, normalized reason set,
meaningful retry/reset boundaries, enumeration, and input/control policy context.
Keep per-refresh counts and state which events or gates were not assessed.

Recording diagnostic facts remains possible during an H13 source or global
pause, as with existing requirement bookkeeping. It starts no fetch, artifact
verification, projection, or stage output. Actual artifact verification retains
its separate controlled execution admission. The external operator hold still
prevents an ordinary cycle; absence of observations during that hold remains
an explicit history gap.

Initially observe at most eight events per cycle, with at most 64 membership
rows and 64 watch associations or distinct watches per event. These are
implementation bounds, not workload calibration. Oversized events are
unassessed; truncated membership cannot establish that an event is unblocked.
Do not write another transition merely because a countdown or usage counter
changed. Doctor reads retained observations without refreshing them.

## Why

Current blockers and request receipts cannot explain earlier waits. Retaining
observed changes adds useful history without recording every skipped page.
However, historical dispatch, robots, per-request limits, and process-local
state are decided by other gate owners. Clear sampled facts do not establish
continuous eligibility between samples, across restart, or during downtime.

## Consequences

This slice reports observations, not continuous intervals. Eligible service
age and proven stage progress remain unknown where unsupported. Retained
history survives restore without being relabeled as a new check. Later exact
eligibility accounting requires shared decisions from every relevant gate and
explicit interval boundaries; summing sample gaps is not an alternative.

The saved catalog high-water mark gives each pass a finite cohort, so continuing
new arrivals cannot displace earlier events. Source-event references own this
history; aliases do not reset it. The bounds limit returned catalog/watch rows,
not the elapsed time of underlying SQL aggregates.

## Links

- [Scheduling contract](../../docs/reference/scheduling.md#reporting-and-acceptance).
- [History and recovery plan](../../docs/plans/history-and-recovery.md).
- [Current blocker facts](0017-report-current-event-blockers.md).
