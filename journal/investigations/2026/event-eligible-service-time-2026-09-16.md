# Measure eligible waiting before enabling no-progress alarms

Date: 2026-09-16 UTC  
Type: Proposed investigation; no implementation or operating acceptance  
Related: [Scheduling reporting contract](../../../docs/reference/scheduling.md#reporting-and-acceptance), [event completion](../../../docs/plans/recovery/README.md#event-completion-extension)

## Recommendation

This is not a safe counter-only increment. Saved blocker observations cannot
establish continuous eligibility. Implement an acquisition waiting clock only
after extracting shared, read-only request eligibility and defining durable
operator-hold control. Keep interpretation and whole-event eligible age unknown
until their own admission gates have equivalent coverage. Acquisition time must
be labeled explicitly, not substituted for whole-event age.

The clock measures time an unfinished event waits while at least one acquisition
obligation is eligible, including time another event receives service. Issued
request duration, request counts, and fair-turn position are not substitutes.
Keep the existing issued-service and qualified-progress receipts separate.

No threshold is proposed from observed throughput. First implement explicit
test/shadow policy values and fake-clock acceptance. Production alarm objectives
still require the workload calibration specified by the scheduling contract.

## Authority and gaps in the current code

| Authority                                                   | Current owner                                           | Required seam                                                                                                                                                  |
| ----------------------------------------------------------- | ------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Source enabled, watch due/retry, pressure, allocation       | `schedule/fairness.py`, `schedule/cycle.py`             | Share gate decisions before fair selection; `_eligible()` alone is insufficient.                                                                               |
| Controls, exact request limits and historical request guard | `fetch/controls.py:issue()`                             | Use the same side-effect-free decisions for measurement and final issuance; retain atomic admission/debit.                                                     |
| In-flight host, cooldown, day requests/bytes                | `fetch/politeness.py:Gate.acquire()` and `release()`    | Separate assessment from mutation; notify the clock immediately before acquire/release changes. In-flight ownership is currently process-local.                |
| Robots rules and expiry                                     | `fetch/robots.py:policy()`                              | A bounded cached-policy assessment that never fetches or repairs artifacts. Missing/expired proof cannot establish eligibility.                                |
| Historical source/year/parent/capture gates                 | `history/backfill.py`, `history/origin_dispatch.py`     | Share exact candidate eligibility with dispatch. A selected dispatch nonce is an execution condition, not proof that every unselected candidate is ineligible. |
| Successful event progress                                   | `state/event_progress.py`, `schedule/event_progress.py` | Use qualified progress receipts; reconcile successful operations whose progress relationship is still unknown.                                                 |

`fetch.controls.issue()` and robots/historical dispatch check more than scheduler
hints. Never probe eligibility by calling `issue()` or `Gate.acquire()`: both can
spend budget. Do not duplicate their rules in a reporting module.

The ordinary data writer holds `state.lock`; controls deliberately bypass that
long lock. `control_state.revision` detects even a pause/resume pair between two
checks. Control event timestamps currently originate before lock acquisition,
so they are not exact commit boundaries under contention. A first implementation
can discard a whole interval when its control revision changes, rather than
invent a split timestamp. Capture interval boundaries under the existing lock
order; never hold `control.lock` during a sleep or network operation.

The `operator-hold` file is a Nix service **startup** condition. It is sampled by
reporting but is not an in-process admission gate or a durable transition log.
Two absent-file samples do not exclude a hold created and removed between them.
Before claiming hold-excluded age, define an owned hold operation using durable
pause-all control, with the startup marker as its companion. Existing manual
marker manipulation remains unjournaled and cannot establish exact history.
This needs an explicit accepted operating rule; it is not already implemented.

## Smallest executable design

1. Extract a pure request assessment, reused by issuance, returning
   `eligible`, `blocked`, or `unknown`, reasons, dependency identity and the next
   time boundary. Keep mutation and final rechecking in issuance. This requires
   source gates and cached robots authority as well as host/watch fields.
2. Add a cycle-owned bounded waiting observer, with explicit operations such as
   `begin_phase`, `before_change`, `after_change`, `checkpoint`, and `end_phase`.
   No generic database callback or separate polling daemon is needed. Hook the
   acquisition loop, request acquire/release, watch refresh, historical dispatch
   changes, and phase transitions. Unknown mutation paths must close coverage,
   not silently leave a positive interval open.
3. Persist current counters and interval identity transactionally; append one
   bounded summary per monitored event/cycle plus meaningful state transitions.
   Reuse request and progress receipts instead of per-page skip logs. A separate
   read-only report consumes closed intervals and never advances the clock.

Start with a bounded, fairly rotated cohort and bounded verified membership.
Bind each episode to source/ref, enumeration, input/epoch and measurement policy.
Oversized or incompletely assessed events remain unknown. Cohort rotation is an
explicit coverage gap; the implementation must not claim continuous fleet-wide
coverage from eight sampled events. Test bounds are not production defaults.

For each event, union its eligible obligation intervals: two ready pages do not
earn twice as much age. Shared requests may make several events eligible; fleet
totals are event-seconds, not unique request-seconds. Issued service still belongs
to its selected owner. Fairness rank and losing a turn must never stop the waiting
clock. The selector's `visited_watches` bookkeeping cannot by itself prove a
safety gate was closed; expose any once-per-cycle service restriction separately.

At each known gate-changing operation, close the prior interval **before** the
change, perform the transaction, then reassess. While another host's request is
running, eligible events continue accumulating waiting time. The busy host's
obligations do not. Count no time beyond a known eligibility boundary without
reassessment: retry expiry, cooldown, pause expiry, day reset, robots expiry, or
phase deadline. A blocked interval may end at that boundary, but must not become
eligible automatically without checking its other gates.

Use an injectable monotonic elapsed clock alongside aware wall timestamps.
Unexpected wall/monotonic divergence makes the crossing interval unknown; it must
not create eligible age by jumping the wall clock. Stable control revisions at
both boundaries are required. A revision change makes the crossing interval
unknown, even when the final pause state matches the initial one. Future work may
record authoritative control commit boundaries to avoid that loss of precision.

Close allocation phases explicitly. Worker shutdown and offline phases are
inactive, not inferred eligible time. A crash leaves the last open interval
unknown; a restart never credits it from `now - saved_at`. Retain already closed
history. Enumeration, policy, epoch, or unresolved gate-proof changes end the
episode without pretending successful progress occurred. Legacy discovery-to-now
eligible age stays null.

Reporting should expose the observation origin, closed eligible waiting seconds,
proven blocked seconds by reason, inactive periods, unknown intervals and current
coverage. Exact eligible age is available only for a fully covered declared
interval. With gaps, the accumulated eligible waiting seconds are a lower bound
over recorded intervals, never an exact age since discovery. The wall-age field
continues independently.

## Defensible no-progress alarm

For the first increment, evaluate an alarm only within a completely covered
episode, with a fresh definitive unfinished obligation, current eligible gates,
an explicit threshold, and no unresolved successful operation since the last
qualified progress receipt. Otherwise report `unknown`, `blocked`, `inactive`, or
`suppressed_by_hold`, with the applicable reason. A new observation origin is
not a historical progress timestamp.

Reset the episode's time-since-progress only for qualified successful progress.
Failed requests, repeated identical observations, restored old artifacts,
enumeration shrinkage, and issued service do not reset it. An operation awaiting
qualification makes the alarm unknown; absence of a qualified receipt alone is
not proof that no success occurred. A hold suppresses an already crossed alarm
threshold while preserving counters and wall age. Unknown intervals do not erase
history but prevent an exact whole-episode alarm claim in this initial design.

The result names the requirement, last qualified progress, observed failures or
guard, last remedy and next permissible action. Emit a report/diagnostic result;
external notification is a separate operating choice.

## Minimum fake-clock acceptance

| Scenario                                                       | Required assertion                                                                                                                                                                             |
| -------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Two waiting events; continuing new arrivals                    | The older eligible event's waiting time increases while another event is served; turn bookkeeping cannot hide starvation. Demonstrate the existing finite service bound under stated capacity. |
| Host busy, cooldown, retry and daily cap                       | Exclude the exact blocked interval; check retry/cooldown boundaries and UTC day reset; byte charge uses the actual request day even when release crosses midnight.                             |
| Pause/resume between checkpoints                               | Revision change yields unknown time, never guessed eligible seconds. A stable observed pause and its expiry remain distinct.                                                                   |
| Global hold after threshold                                    | Alarm is suppressed immediately by the owned durable control; counters survive resume without a request burst. Legacy marker-only history remains unknown.                                     |
| Source disabled, parser pause, year/parent/capture rejection   | Candidate hints cannot establish eligibility; actual shared source authority wins.                                                                                                             |
| Shared request and multiple pages                              | Each event gets the union of waiting intervals; only the selected owner gets issued service.                                                                                                   |
| Repeated failure beside healthy work                           | Failures consume service but never reset progress age; independent healthy work still completes.                                                                                               |
| Successful but unqualified operation                           | Alarm becomes unknown until exact progress qualification resolves; initial positives and restored artifacts do not fabricate progress.                                                         |
| Restart, phase end, rotation, changed input or wall-clock jump | Preserve closed counters; label inactive/unknown gaps; never extrapolate an open interval.                                                                                                     |
| Bounded assessment and reporting                               | Exhausted metadata/artifact budgets produce unknown; reports perform no writes, artifact recovery or implicit time accrual.                                                                    |

## Scope for the next decision

Approve the durable hold protocol and acquisition-only measurement definition
before implementation. Then implement shared eligibility and the bounded interval
recorder together; a recorder based only on current scheduler hints would be
misleading. This is a multi-module increment with focused integration tests, not
a small reporting patch. It does not finish whole-event eligible age, interpretation
waiting, continuous unobserved fleet coverage, or production threshold calibration.
