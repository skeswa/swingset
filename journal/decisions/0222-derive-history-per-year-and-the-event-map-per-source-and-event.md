# D-0222: Derive history per year and the event map per source and per event

Recorded: 2026-09-29  
Decided by: agent, 2026-09-29, under D-0175; source: phase 3 of the [architecture plan](../../docs/plans/architecture.md) (findings F17, F18, F19, F23)  
Topic: Derivation units  
Supersedes: in part, [D-0199](0199-contain-build-failures-and-pace-global-units.md) (history pacing); in part, [D-0210](0210-pace-the-inventory-like-history.md) (the history lift and the starvation breaker)  
Superseded by: —

## Decision

The two global projections are split, and their scopes are retired (rows and
generations stay; nothing schedules them; migration 35 deletes their queue
rows):

- `project/history/all` becomes one `project/year/<yyyy>` unit per year. A
  year sets `event_id` on the registry placements reported in it (at or
  after `history_start`), the `sheets_partial` tier of its events, and its
  `coverage` rows. The live `coverage` table stays.
- `project/map/all` becomes `project/mapping/<source>`, which writes that
  source's `source_event_proposals`, and `project/resolution/<event_id>`,
  which writes the event's `source_event_map` rows and its placeholder
  event row. A resolution reads the proposals naming its event as content
  and only waits for the mappings. Neither re-projects events or reconciles
  placements globally; events re-run through their resolution, and the year
  owns the association.
- `project/inventory/all` stays one global unit, paced by
  `inventory_interval_seconds`. Only a calendar replay or a newer calendar
  generation lifts that wait; the history lift and starvation breaker are
  gone. Years are not paced; `history_interval_seconds` is accepted and
  unused.
- A unit's content inputs never include a column it writes (F23). A
  dancer's reconcile that moves `event_id` reaches the years through the
  dancer replay epoch.
- An alias or finding-review edit queues the inventory and every year;
  currency decides which run.

## Why

The global units made one local defect global. History wrote 201,073 rows in
one transaction, and each new generation re-queued all 2,932 links. The
splits are exact: association joins by month (same year), and tier and
coverage group by event year. Index scopes are pages (846 rows for 464
listings), so the map splits per source, not per index page. The inventory
matches editions across December and January and reads every non-registry
event, so splitting it is a later plan item.

## Alternatives and consequences

A resolution that depended on its mapping's generation would re-mint every
resolution of a source when one listing changed. A dependency drawn from its
own map rows moved during its own run. New events reach the years up to one
inventory interval later.

## Links

- [State reference, year units](../../docs/reference/state.md#year-units)
