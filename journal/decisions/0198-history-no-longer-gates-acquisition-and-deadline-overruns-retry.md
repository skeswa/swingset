# D-0198: History no longer gates acquisition, and deadline overruns retry

Recorded: 2026-09-24  
Decided by: agent, 2026-09-24, under D-0183  
Topic: Acquisition  
Supersedes: the history clause of the projection gate in [D-0195](0195-answer-the-projection-question-once-per-pass.md)  
Superseded by: in part, [D-0211](0211-a-derivation-that-raises-retries-on-a-clock.md) (the retry clock covers every exception a derivation raises)

## Decision

A pending `project/history/all` no longer closes phase two. With derivations
on, that unit only runs `finalize_history`, which writes
`registry_placements.event_id`, `events.coverage_tier` and `coverage`. The
year gate compares `_inventory_digest`, which reads none of them. Inventory,
map, calendar, source-index, dancer and unmapped source-event units still
block. The legacy path, which rebuilds the inventory, still blocks too.

A unit whose write overruns its bound is now `transient`, not `blocked`. It
retries after 15 minutes, doubling per consecutive overrun, at most six hours
apart. The history unit alone gets a 300-second write bound instead of 45
seconds. It stores ~200k rows in one write, which took 55 to 82 seconds on a
copy of the 2026-09-24 database, and D-0199 limits it to twice a day.
A control arriving during that write fails at its 60-second lock timeout unless it passes `--lock-timeout 330`; `systemctl stop` kills the process after 45 seconds, and the write rolls back either way.

## Why

On 2026-09-24 at 08:02 UTC the history unit overran the bound, a blocked
outcome with no retry path. It then held the gate shut, so no EEPro,
scoring.dance or WDR sheet was ever fetched, although all 17 years were
accepted. The gate was also circular: every fetched sheet re-queues history,
so each fetch closed the gate for the next one. An overrun rolls back whole
and says nothing about the inputs, so waiting for an input change could wait
forever.

## Links

- [Stability plan](../../docs/plans/stability.md)
