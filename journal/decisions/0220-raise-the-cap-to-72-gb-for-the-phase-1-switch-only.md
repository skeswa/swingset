# D-0220: Raise the cap to 72 GB so the phase 1 switch can drain

Recorded: 2026-09-29  
Decided by: owner, 2026-09-29; source: "I like all the suggestions- proceed" (cap for the phase 1 switch, the gate waiver and the deploy)  
Topic: Retention  
Supersedes: the 64 GB figure of [D-0196](0196-raise-the-database-size-cap-to-64-gb.md) and, in part, [D-0208](0208-keep-the-64-gb-cap-until-closure-collection.md) and the plan's phase 0 "no cap raises"  
Superseded by: in production, [D-0233](0233-raise-the-size-cap-to-76-gb-until-phase-4.md): 76 GB until phase 4

## Decision

`retention.max_database_bytes` is 72 GB from the phase 1 switch. It is a
standing limit, not a temporary one: the file never shrinks without
collection and reclaim, so lowering it would re-pause the worker at once. It
comes back down only after the invalidation plan's collection of superseded
rows (step 3's successor in phase 4) and a reclaim.

While the switch drains, `build_interval_seconds` is 24 hours. Builds during
the drain withhold links, publish nothing (dry run) and cost about 0.27 GB each.

When the file reaches 72 GB, the worker pauses as before. There is no further
raise until quota and Mac space are freed first; the live file must stay under
about 80 GB so a byte-copy rollback still fits on the Mac.

The owner also waives the invalidation plan's gate (24 clean hours before the
switch): the worker is paused at the cap and cannot run clean, and the switch
is what removes the churn. Open decision 3 is answered by measurement: a
code-only deploy grows the file 84 MB, under the 100 MB target.

## Why

Projection churn grew the file from 46.6 GB to 64.7 GB in two days, and the
cap paused all work at 02:00 UTC on 2026-09-29. Phase 1's output cutoff and
inventory pacing remove that churn, but the switch needs 1.0 to 2.6 GB to
drain from this state and cannot run while the cap holds the worker paused.
Waiting for phase 4 would stop collection for weeks. At production's measured
2 to 5 GB a day after the switch, 72 GB lasts one to three days, which covers
the parse and link drains but may not cover the canary.

## Links

- [Independent check and conditions](../../docs/plans/architecture.md)
