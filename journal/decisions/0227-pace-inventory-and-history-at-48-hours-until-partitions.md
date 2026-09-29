# D-0227: Pace inventory and history at 48 hours until partitions ship

Recorded: 2026-09-30  
Decided by: owner, 2026-09-30; source: "I like these suggestions" (48-hour pacing, phase 4 next)  
Topic: Scheduling  
Supersedes: the 12-hour `history_interval_seconds` and `inventory_interval_seconds` of D-0199 and D-0210, in production  
Superseded by: in part, [D-0228](0228-pace-the-inventory-weekly-until-partitions.md): the inventory interval is 7 days

## Decision

Production sets `history_interval_seconds` and `inventory_interval_seconds`
to 172,800 (48 hours) until phase 3's year partitions and read-set
dependencies are deployed; that switch returns them to its own values.

## Why

After the phase 1 drain the file still grew about 2.9 GB a day (65.5 to
68.6 GB between 2026-09-29 10:16 and 2026-09-30 13:01 UTC), against a 72 GB
cap. Each inventory generation re-mints every event (about 3,080), every link
(2,934) and history, about 1 to 1.5 GB, and phase 1's cutoff correctly
refuses to keep generations whose inputs changed. Pacing one cascade every
two days roughly halves the growth; the event list refreshes less often,
which a weekly dataset tolerates. Scheduling settings re-queue nothing.

## Links

- [Target architecture, phases 3 and 4](../../docs/plans/architecture.md)
