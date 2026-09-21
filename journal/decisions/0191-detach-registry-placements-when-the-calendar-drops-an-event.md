# D-0191: Detach registry placements when the calendar drops an event

Recorded: 2026-09-21  
Decided by: agent, 2026-09-21, under D-0183  
Topic: Data model  
Supersedes: —  
Superseded by: —

## Decision

When a projection scope stops asserting an event and no other scope owns it,
the writer first sets `registry_placements.event_id` to null for that event,
then deletes the row. The inventory projection that runs after every calendar
change creates the registry-backed event for the same series and month and
links the placements to it again. Nothing else about scope replacement changes.

## Why

`registry_placements.event_id` references `events` without a cascade. The
live WSDC calendar lists upcoming events only, so an event leaves it once it
has passed. `2026-09-trilogy-swing` ran 10 to 13 September 2026, the registry
already reported one placement for it, and the first fresh calendar capture in
eight days (2026-09-21 22:04 UTC) no longer listed it. Deleting the row failed
with `FOREIGN KEY constraint failed`, the calendar unit was blocked, the cycle
reported `failed=true`, and the inventory and history units behind it could
not run. Two more events are in the same position. This would have recurred
every week as events pass. It stayed hidden while nothing was fetched
(D-0190). Reproduced and verified on a scratch copy of the live database.

## Alternatives and consequences

Keeping the row was rejected: an event no scope owns would never be updated
or removed. A cascade was rejected: a registry placement is evidence in its
own right and must outlive the listing. Between the delete and the inventory
run the placement has no event, which is the honest state.

## Links

- [Receipt](../evidence/runtime/candidate-014-2026-09-21/receipt.json)
