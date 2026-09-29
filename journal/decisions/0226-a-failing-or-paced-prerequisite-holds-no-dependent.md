# D-0226: A failing or paced prerequisite holds none of its dependents

Recorded: 2026-09-30  
Decided by: agent, 2026-09-30, under D-0175; source: the phase 3 review of the [architecture plan](../../docs/plans/architecture.md)  
Topic: Derivation readiness  
Supersedes: the rule that a unit waits until every prerequisite is current (`derivations._ready`), in part  
Superseded by: —

## Decision

Readiness takes a prerequisite as settled at its current pointer, or at its
absence when it never materialized, when it is not current because it is
quarantined, waits for input, backs off to a later retry (its `work_queue`
row), or the pacing gate holds it on purpose (the inventory inside its
interval). Its dependents run against that pointer and re-run when it moves.
Each settled prerequisite is named in the derive tick's
`settled_prerequisites`; the failing unit keeps its own queue alarms.

## Why

Readiness was transitive through the graph. After one dancer change the
paced inventory stayed stale for up to 12 hours, so every year waited, and
through the years every link; one quarantined dancer, calendar or index page
froze the inventory, and one quarantined event its year (I1, I5). The
dependents read committed rows, so running them on the current pointer is
correct, and currency re-runs them when the prerequisite lands.

## Alternatives and consequences

Making each year read its events' rows as content, instead of waiting for
event units, would also cut the chain, but it changes the year's inputs and
the closure path's anchor walk. A dependent may now run twice: before and
after the prerequisite's next generation. That costs CPU on a paced
inventory's re-run, which cuts off when its output is unchanged (F21, F28).

## Links

- [State reference, year units](../../docs/reference/state.md#year-units)
