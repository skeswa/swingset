# D-0044: Record closed acquisition waiting intervals conservatively

Recorded: 2026-09-17  
Decided by: agent  
Topic: Event-completion timing and local alarms  
Supersedes: —  
Superseded by: —

## Decision

Implement a bounded acquisition observer using shared request gates, monotonic
elapsed time, and closed durable intervals. Record whole-event and legacy age as
unknown. Separate actual selected service from qualified successful progress.
Configure event alarm objectives explicitly; leave production values unset
until measured. Permit a separately labeled lower-bound breach when closed
eligible waiting already exceeds an objective, current gates are eligible, and
all possible intervening service/progress resets remain accounted for. Keep
historical-dispatch eligibility unknown until its complete proof is connected.

Observe marker changes conservatively through the state directory's modification
witness. A control revision, marker witness, wall-clock discontinuity, dependency
change, or unobserved mutation discards the crossing interval as unknown. Preserve
closed history across interruptions. Emit diagnostics without external messages.

## Why

Saved blocker samples cannot establish continuous request eligibility. Failed
requests consume service but do not prove completion progress. The accepted
extension requires both distinctions, while the current historical dispatcher
and manual hold marker do not expose complete time histories. The
[prior investigation](../investigations/2026/event-eligible-service-time-2026-09-16.md)
identifies those gaps. This increment implements the ordinary acquisition seam
and preserves their uncertainty rather than bypassing them.

## Alternatives

- Infer age from discovery or last request. Rejected because pauses, budgets,
  retries, and failed attempts would misstate successful-progress age.
- Treat scheduler candidates as fully eligible. Rejected because robots,
  pressure, exact controls, and historical admission remain independent gates.
- Extrapolate saved open intervals after restart. Rejected because elapsed
  process time and intermediate gates are not known.
- Enable a default production objective now. Rejected because fixed-cohort
  service under ordinary host limits has not been measured.

## Consequences

Ordinary acquisition gets testable clocks and local alarm evaluation without
additional requests or increased budgets. Unknown intervals make the counters
lower bounds. They prevent an exact age claim but need not hide a proven
lower-bound threshold breach. Changed dependency identity invalidates inherited
alarm bounds until a new corresponding receipt establishes continuity. Marker-directory changes
may discard more time than necessary. SQL and filesystem operations have no hard
deadline. Full history, interpretation timing, historical dispatch proofs,
production calibration, deployment, and publication remain separate work.

## Links

- [Exact local timing behavior](../../docs/reference/event-timing.md)
- [Scheduling contract](../../docs/reference/scheduling.md#reporting-and-acceptance)
- [Implementation and fresh checks](../investigations/2026/event-timing-2026-09-17.md)
