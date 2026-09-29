# D-0210: Pace the inventory like history

Recorded: 2026-09-26  
Decided by: agent, 2026-09-26, under D-0175; source: phase 1 review of the [architecture plan](../../docs/plans/architecture.md)  
Topic: Scheduling  
Supersedes: —  
Superseded by: in part, [D-0222](0222-derive-history-per-year-and-the-event-map-per-source-and-event.md) (history is retired: no history lift or starvation breaker; the inventory pacing stays)

## Decision

`project/inventory` re-materializes at most once per
`inventory_interval_seconds` (production: 12 hours), beside the history gate
of D-0199 (`schedule/pacing.deferred`). A due `project/history` lifts that
wait once it has consumed the inventory (materialized at or after it). A
history whose latest attempt failed is not due. Both gates count a cutoff as
a materialization. Unset the interval and the inventory runs whenever stale.
A calendar replay epoch newer than the one the inventory last read lifts the
wait too (`pacing.calendar_replayed`), and so does a calendar generation
newer than the inventory's output (`pacing.calendar_regenerated`): either
rewrote live `events` columns the inventory enriched (finding F2), and the
readers below would otherwise record calendar-only values.

Starvation breaker: history needs a current inventory, so a dancer change in
every cascade would hold it back forever. Once an inventory has materialized
at or after history came due, that cascade was interrupted, and the
inventory runs whenever it is stale until history lands, as before this
record. The watchdog mirrors the gate and clocks a due history from the
first inventory run since it came due; later runs do not move that clock,
so starvation fires `[stale-progress]`.

An inventory the gate holds (not current, made within its interval) and the
map behind it do not count as pending for the phase-two gate or
`accept-year`: they read the committed event list, a fact (I5). A current
inventory holds nothing. Only a run that names its scheduler (the cycle,
`accept-year`) holds one. The cycle counts the gate's refusals by reason.

## Why

Any dancer change mints a new inventory and, by the closure-stable rule
(D-0205), a new map, every event and source event, history and every link:
on the 16:10 copy, 5 identical cascades on 2026-09-25, about 0.43 GB each.
Pacing to two a day, plus one per calendar replay or calendar generation
the inventory has not read, cuts the state's growth from about 2.9 to about 2 GB a
day (the plan's steady-state paragraph). A first version let any due
history re-run the inventory, which restarted the cascade on each dancer
change, and a held inventory closed phase two for every year.

## Alternatives and consequences

Batching dancer admissions changes parse and admission, not one gate.
Letting history run on a stale inventory breaks the closure-stable rule.
New events and dancer facts reach history and links up to 12 hours later;
an interrupted cascade costs up to one more interval before the breaker
unpaces the inventory. A year accepted while the inventory is held records
the committed list; if the next inventory changes it, the year closes until
accepted again.

## Links

- [Scheduling](../../docs/reference/scheduling.md)
