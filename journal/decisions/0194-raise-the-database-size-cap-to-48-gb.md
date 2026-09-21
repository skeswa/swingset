# D-0194: Raise the database size cap to 48 GB

Recorded: 2026-09-23  
Decided by: agent, 2026-09-23, under D-0183  
Topic: Retention  
Supersedes: the 32 GB figure in [D-0186](0186-raise-the-database-size-cap-to-32-gb.md)  
Superseded by: —

## Decision

`retention.max_database_bytes` rises from 32 GB to 48 GB. The pipeline
paused itself at 02:56 UTC on 2026-09-23 at 32.6 GB and stayed paused for
three hours, with every cycle finishing in ninety seconds and doing nothing.
The worker's quota has 87 GB free, and the backup's room check refuses a
backup the quota cannot hold, so 48 GB is safe for the disk.

## Why

The file holds live rows, not garbage: reclaim on 2026-09-22 freed 0.3 GB of
24 GB. Each source or override change re-derives every generation before the
old ones can be collected, and the day saw two such re-derivations plus a
third queued by an alias edit. The retention planner that would collect the
superseded generations is killed by the memory limit at 8.3 GB of resident
memory on a database this size, so nothing shrinks the file today. The cap
is a safety limit against runaway growth, not a target, and a pause that
nobody notices is worse than a larger file. Two follow-ups are queued: a
planner that streams instead of holding the graph in memory, and the recipe
scoping that stops whole-graph re-derivation.

## Links

- [Receipt](../evidence/runtime/v5-year-acceptance-2026-09-22/receipt.json)
- [Operation guide, disk space](../../docs/guides/operation.md#disk-space)
