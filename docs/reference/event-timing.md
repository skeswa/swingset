# Observed acquisition waiting time

The local cycle records how long a bounded group of source events waits for an
ordinary request or a proved Archive offer that can pass its gates. This is an acquisition diagnostic.
It does not establish whole-event completion, interpretation waiting time,
continuous fleet coverage, or time before recording began. Deployment and
operating calibration remain separate gates.

## What is measured

Each acquisition phase fairly rotates through up to eight retained source events.
One shared evidence session checks at most 32 members per event, within the
existing JSON, artifact, row, and cooperative elapsed budgets. Parent admission,
enumeration content addresses, missing acquisition evidence, and exact member
watch bindings must verify. Artifact checks enter through existing H13 admission;
a paused observer never prevents independent permitted acquisition. Exhausted or oversized evidence stays unknown.
A supported unavailable page is accounted for separately and earns no missing
acquisition waiting time.

A waiting event accrues once when at least one verified missing request is
eligible. Multiple pages and shared pages do not multiply an event's elapsed
time. Totals across events have units of event-seconds. Issued service remains
attributed to the selected event in its existing receipt. Losing a fair turn or
appearing in the cycle's visited-watch set cannot erase waiting time.

The observer uses the host assessment also used by request acquisition, shared
operator scopes, shared backpressure and expansion gates, source enablement,
watch due and retry times, and bounded verified robots cache reads. An expired,
missing, corrupt, or oversized robots artifact yields unknown eligibility; the
observer never fetches or repairs it. Historical origin offers remain unknown.
Archive waiting has a separate local schema-29 proof described below. It creates
no acquisition authority and does not accept a historical year or page kind.

## Historical Archive offers

The existing dispatcher checks its exact retained plan, year acceptance, page-kind
policy, event mapping, parent readiness and capture alternatives. Its optional
handoff brackets those checks with the schema-29 `history_dispatch_fence` and
existing control revision. It copies only offers matching at most 256 watches
already selected by the bounded observer. No candidate, year inventory, parent
or capture scan is repeated while assessing an interval.

Each immutable proof binds the current connection and run, original watch
identity, offered replay URL, history floor, checked dependency revision and
expiration. A proof can describe advancing an existing watch to a selected
capture; observing it never performs that update. Shared host, robots, source,
retry, backpressure and allowance gates still apply. Missing, expired, changed,
wrong-run or wrong-connection proof remains unknown.

The new revision conservatively covers inserts, updates and deletes to dispatch
and year-inventory dependencies, including source-unit selection, admitted
parents, observations, snapshots, captures, mappings, findings, inputs and work
attempts. Existing operator controls retain their own revision and restricted
write authority. Host and scheduler accounting are assessed through their current
gates; timing's own writes do not invalidate its proof.

At most 64 pending parse units and their latest attempt metadata are inspected
once when the producer begins. The earliest future transient/interrupted retry
caps every proof, even when the unit is unrelated. Larger populations or invalid
retry timestamps withhold proof. This boundary covers a failed earlier capture
becoming eligible for interpretation and thereby preventing a later capture's
acquisition, without a database write. The observation deadline and normal gate
boundaries can expire the proof sooner.

Both opening and closing boundaries recheck the dependency revision. Changed
proof discards the crossing interval as unknown. The dispatcher closes timing
before scheduling mutations. Global invalidation can discard unrelated events'
proofs until a later ordinary offer pass; this deliberately sacrifices coverage.
Closed eligible lower bounds and successful-progress counters remain preserved.
No proof survives a new connection or restart, and no legacy eligible time is
backfilled. Origin, interpretation and whole-event timing remain outside this
slice. Schema 29 and this handoff are local implementation; frozen production
runtime 003 remains schema 28 until a separate validated rollout.

See [D-0070](../../journal/decisions/0070-fence-historical-archive-timing-proofs.md)
and the [implementation evidence](../../journal/investigations/2026/historical-archive-timing-2026-09-17.md).

## Interval boundaries

The acquisition loop and actual HTTP admission/release close intervals before
changing gates. While one host has a request in flight, other eligible hosts'
events can accrue waiting time. A known retry, cooldown, robots-expiry, UTC daily
reset, or acquisition deadline caps its interval. A blocked interval cannot
become eligible solely because its time boundary passes.

Elapsed time uses the injectable monotonic clock. Wall and monotonic differences
larger than 250 milliseconds make the crossing interval unknown. A changed
control revision, input/epoch/enumeration token, or state-directory marker-change
witness also makes the interval unknown. Comparing directory modification time
conservatively detects creating and removing `operator-hold` between endpoints;
unrelated changes can also discard an interval. It cannot reconstruct marker
history from before the observer started.

An observed operator marker is a blocked interval and immediately suppresses
eligible-work alarms. Durable operator pauses remain the existing controls;
this implementation does not grant manual marker changes a fabricated timestamp.
Use durable `pause` controls for an active worker and retain the startup marker
while operationally held. [Operations](operations.md#locks-and-operator-commands)
owns the control commands and their scope.

Recorder suspension around work whose intermediate gates are not observed creates
an unknown gap. Phase closure records inactivity. A later acquisition phase in
the same run may retain that inactive span. Cross-run rotation and a crash with
an open phase create unknown gaps; restart never credits an open interval as
eligible time. Already closed counters and per-run summaries survive restart and
backup. Ordinary snapshot revisions are reverified without resetting the waiting episode,
including failed responses. Dependency identity or measurement-policy changes
start a new episode and retain the previous summary and waiting counters as
lower bounds. They never create successful progress; inherited counters cannot
raise an alarm until a new corresponding receipt establishes continuity.

## State and reporting

Schema 26 adds `event_timing`, `event_timing_history`, and `event_timing_cursor`.
The current row stores closed eligible, blocked, unknown, and inactive seconds;
reason totals; a source/enumeration/input/epoch token; policy; observation origin;
and independent service and successful-progress counters. One summary per
episode and run is updated while that run observes the event. These are
scheduling diagnostics, never completion or request authorities.

`doctor --source SOURCE --source-event SOURCE_REF --json` includes
`acquisition_timing`. Reading the report performs no writes, requests, artifact
recovery, or implicit time accrual. It reports the closed eligible lower bound,
exact observed active time only when fully covered, observation wall age, and
explicit unknown whole-event and legacy eligible ages. These counters are not
age since discovery. Parent doctor reporting still owns discovery wall age and
published progress.

Service-gap and no-successful-progress alarms use separate counters. Issued
service, including a failed request, resets only the service counter. Only a new
qualified progress receipt resets successful-progress time. A successful
operation still awaiting qualification makes the progress alarm unknown. Enumeration changes,
restored artifacts, retries, and scans do not count as progress.

A threshold alarm requires a current unexpired eligible assessment, unchanged
dependencies and control revision, and a configured positive objective. An exact
verdict also requires a completely covered episode. With observation gaps,
`lower_bound_exceeded` proves a breach if already closed eligible waiting exceeds
the objective and receipt continuity remains established. Unknown gating time can
only increase actual waiting; it cannot erase an already verified lower bound.
A below-threshold lower bound with gaps remains unknown.

Service continuity uses actual selected-owner request receipts. Successful-progress
continuity requires no unresolved possible success since the episode's initial
operation frontier or last qualified progress. Initial old successes do not
suppress a new episode. Shared request identities are included; unqualified
same-source interpretations remain conservative unknowns because they may carry
aggregate support. Unknown success suppresses the progress alarm independently
of the service alarm. Dependency identity changes invalidate both inherited
bounds until a new respective service or progress receipt resets its clock. Current holds suppress alarms while
preserving counters. Inactive, blocked, incomplete, and unknown observations keep
their own states. Alarm diagnostics identify the acquisition requirement,
qualified progress, blocker or guard, and next action. They send no notification.
The report consumes closed observations; it does not certify worker liveness or
extrapolate past the saved boundary.

The optional scheduling values are
`event_acquisition_service_alarm_seconds` and
`event_acquisition_progress_alarm_seconds`. Both default to unset. Explicit test
values exercise the alarm logic; production objectives still require fixed-cohort
demand and service measurements. Conservative gaps retain unknown exact age; sufficient verified waiting can
still demonstrate a lower-bound breach. These checks do not calibrate production
objectives or establish continuous fleet service.

See [D-0044](../../journal/decisions/0044-observe-bounded-acquisition-waiting.md) and
[the implementation receipt](../../journal/investigations/2026/event-timing-2026-09-17.md).
