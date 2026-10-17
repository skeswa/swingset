# D-0262: An event state no longer holds gets no release partition

Recorded: 2026-10-17  
Decided by: agent, 2026-10-17, under D-0172; source: the independent check that blocked `cand_060e7514cab84a67`  
Topic: Publication  
Supersedes: D-0224's rule that selection reads current pointers only  
Superseded by: —

## Decision

- Partition selection reads which events exist in state. An event with no
  `events` row gets no partition, even when a current generation still lists
  it, so no header of it publishes. Its old partition row is removed.
- If state still has results for such an event (a row its `event` or
  `resolution` scope owns, a contest or a judge), its partition is withheld
  (`event_not_in_state`) instead.
- D-0251's baseline stand-in and D-0254's listing header apply only inside a
  partition, so neither brings a dropped event back. A dropped baseline event
  with results is a lost result event to the gate, unless it moved.
- Units of a dropped event are not deleted. As with every scope, the catalog
  keeps them, and their generations are empty.

## Why

The candidate published six empty 2024 trial twins beside their real
events. D-0260's merge had deleted their rows after the inventory's 09:54
generation captured them, most likely when their resolution units re-ran at
10:36-10:42 (**unverified**; no changelog row was checked). The inventory does not depend on those
units, so its current generation still listed them. Pointers alone cannot
tell a stale header from a real one; state can. Measured read-only on
2026-10-17: 144 partitions drop, 138 of them already empty, and exactly
those six publish nothing any more.

## Alternatives and consequences

Re-running the inventory when another unit deletes a row fixes the source,
but it changes project scopes (maintenance item 38).

## Links

[Build: partitions](../../docs/reference/build.md#partition-artifacts)
