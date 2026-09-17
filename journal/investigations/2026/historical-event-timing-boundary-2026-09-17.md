# Historical event timing boundary, 2026-09-17

The read-only implementation audit found that the current measurement token
cannot safely authorize historical Archive waiting intervals. No runtime change
or schema migration was made. Historical eligibility, interpretation timing and
whole-event eligible age remain open requirements. This result does not alter
frozen runtime 003 or its production rollout.

## Missing dependencies

`history.backfill.offers` evaluates `_candidate` against the retained platform
plan. Dispatch repeats the checks before scheduling an exact watch. A future
timing handoff must preserve those checks across an interval, including:

| Dependency                                            | Existing check                                                 | Missing interval fence                                                               |
| ----------------------------------------------------- | -------------------------------------------------------------- | ------------------------------------------------------------------------------------ |
| Exact original request, capture and source kind       | Candidate WatchSpec and chosen Capture                         | A durable or process-scoped proof must bind the exact offer and current run.         |
| Event mapping, dates and explicit year acceptance     | Mapping equality and `phase_two_gate`                          | These inputs are not all covered by the event pressure token.                        |
| Capture ordering and all usable alternatives          | `next_capture` over the bounded planned alternatives           | Capture-set changes need an invalidation boundary.                                   |
| Parent readiness                                      | `_parent_ready`, retained interpretation and child declaration | Parent selection and pending interpretation state need complete dependency coverage. |
| Pending parse attempts                                | `snapshot_state` and retry eligibility                         | The earliest retry can change eligibility without a write.                           |
| Host, controls, due time, robots and shared allowance | Existing request and observation gates                         | Existing gates must still apply after the historical proof passes.                   |

A concrete counterexample follows from the current branches in
`history.captures.snapshot_state`: an HTTP-successful capture with failed parsing
and a pending transient attempt is incomplete before its retry time. An offer
may therefore select a later capture. Once the pending attempt becomes eligible,
the earlier capture is waiting and `next_capture` stops that later acquisition.
No new snapshot or changed event measurement token is needed for this transition.
This is a code-path finding, not a newly executed acceptance test.

The token in `schedule.event_pressure.token` covers event revision, enumeration,
global support epoch, input bundle and measurement policy. Source gap revisions
add coverage for generations and snapshots, but still do not provide one fence
for all capture, mapping, source-unit selection, queue and retry dependencies.
Reusing either token alone would overstate eligibility.

## Safe next implementation boundary

A bounded handoff needs an immutable dependency capsule or dispatch-specific
invalidation fence, plus the earliest applicable temporal boundary. It must be
checked at both interval endpoints and bound to the actual current run. A
fallback can decline timing whenever it cannot bound a dependency; it must not
claim successful service or reset successful-progress clocks.

Calling the existing full candidate and retained-plan logic from every observer
sample is not an acceptable shortcut. Those paths may read source-wide capture
or query populations and accumulated snapshots. Origin `retained_proposal` also
scans source captures and queries and requires the dispatcher run intent and
cadence. Origin and interpretation timing should remain separate work.

The coordinator chose to record these concrete missing fences in this bounded
task instead of adding schema 29 or tests that merely repeat the existing unknown
state. No tests, full-suite receipt, deployment or publication are claimed for
this documentation-only audit. See
[D-0066](../../decisions/0066-require-complete-dispatch-fences-for-historical-timing.md).

A later local implementation addresses the Archive slice with a new dependency
fence and retry boundary; see [the separate implementation receipt](historical-archive-timing-2026-09-17.md)
and [D-0070](../../decisions/0070-fence-historical-archive-timing-proofs.md).
The audit above remains the reason the earlier schema-28 token alone is
insufficient. Origin and broader timing scopes remain open.
