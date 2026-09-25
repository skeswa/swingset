# D-0196: Raise the database size cap to 64 GB

Recorded: 2026-09-23  
Decided by: agent, 2026-09-23, under D-0183  
Topic: Retention  
Supersedes: the 48 GB figure in [D-0194](0194-raise-the-database-size-cap-to-48-gb.md)  
Superseded by: in part, [D-0208](0208-keep-the-64-gb-cap-until-closure-collection.md): the cap's exit condition is invalidation step 3

## Decision

`retention.max_database_bytes` rises from 48 GB to 64 GB. The file was
39.2 GB at 12:36 UTC, seven hours after the 48 GB cap was set, with the
candidate 016 re-derivation still to write its generations. The worker's
quota has 106 GB free, the backup's room check protects the disk, and a
silent self-pause costs more than a larger file. The number is a safety
limit against runaway growth, not a target; it comes back down once the
planner collects superseded generations.

## Why

Same as D-0194: the retention planner is killed by the 8 GB memory limit on
a database this size, so nothing collects the generations that each full
re-derivation adds, and three deploys in two days each re-derived the whole
graph. Both fixes are queued as maintenance.

## Links

- [Receipt](../evidence/runtime/v5-year-acceptance-2026-09-22/receipt.json)
