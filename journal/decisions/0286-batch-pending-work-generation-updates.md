# D-0286: Batch pending-work generation updates

Recorded: 2026-11-11  
Decided by: agent, 2026-11-11; source: D-0172  
Topic: Native work-generation triggers  
Supersedes: —  
Superseded by: —

## Decision

Native migration 40 runs the pending-work enqueue and invalidation callbacks
once per statement over their changed rows. Each pending-work primary key
appears at most once in that set. Insert a missing generation at one; increment
an existing generation once per changed pending row. An update with no matching
generation still creates nothing. Preserve retry fields and function grants.

Publication and generation-insert history fences stay per row. Counted history
updates keep their exact revision deltas. Empty statements write no fence row,
and rollback or a publication refusal leaves no changes. Keep native 37/38/39
SQL unchanged. Native 38 remains the sole older read-only checkpoint exception
under D-0285; ordinary workers require the current schema.

## Why

The native-39 diagnostic still exceeds sixty seconds. Its enqueue row callback
causes thousands of separate generation statements; their history function
uses 41.526 seconds of self time. Batching the callbacks lets the already
counted fence run once for the generation set. The write budget stays fixed.

## Links

[Measurement and gate](../../docs/plans/postgres-migration/7-stage-2.md#batch-the-measured-nested-generation-updates)
