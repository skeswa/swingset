# Milestones

Each milestone ends with a published dataset that is strictly better than
the last one.

| #   | Deliverable                                                                                                                                                                                                                                                                                    | Done when                                                                                                                                                   |
| --- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------- |
| M0  | Skeleton: flake, NixOS module, CLI, config, fetch layer with politeness and archive, SQLite state, timers, backup and restore                                                                                                                                                                  | a cycle runs every 15 min on the box; a restore on a clean VM works                                                                                         |
| M1  | Registry mirror: bootstrap sweep with dump cross-check, adaptive daily/weekly new-id probes, trickle refresh, `dancers` and `registry_placements` published                                                                                                                                    | dataset viewer shows both tables; card written                                                                                                              |
| M2  | EEPro: discovery, round parser, canonical model, `events` through `placements` published, name-only linking, `link_candidates` and `review_queue`                                                                                                                                              | a full past event is queryable end to end                                                                                                                   |
| M3  | scoring.dance: parser with WSDC ids; hand-set linker weights; registry confirmation loop; `points_matches_expected`                                                                                                                                                                            | matching registry evidence automatically produces `confirmed` links after publication                                                                       |
| M3b | World Dance Registry: `routeInfo.json` parsers, discovery by overrides                                                                                                                                                                                                                         | the 14 known events parse; a live weekend shows almost all polls as 304s                                                                                    |
| M4  | DCN: node-evaluated payload and PDF parsers, bib backfill from PDFs, JSON endpoint hunt first                                                                                                                                                                                                  | DCN events reach parity with EEPro within the 300 MB/day byte budget                                                                                        |
| M5  | Heats where public, `changelog` documented with examples, suppression path tested end to end, issue templates live                                                                                                                                                                             | every data kind listed in [overview](../overview.md#what-you-can-find-in-the-data) is represented                                                           |
| M6  | Backfill to 2010-01-01 ([backfill](../reference/backfill.md)): Wayback transport, registry-seeded events with month precision, archived calendar captures, Step Right Solutions, platform archives newest first, origin only for gaps by each host's rule; `sealed` state; schema declared 1.0 | every registry occurrence since 2010 has an `events` row; the `coverage` table and card list every year, source, and tier, and archive versus origin counts |

The phase-by-phase detail, per-host budgets, and the operator
conversation that precedes M2 are in [scraping plan](../../journal/archive/scraping-plan.md).

v1 is M0 through M3b. The work packages, their order, and the VM they
run in are in [implementation plan](../../journal/archive/v1-implementation-plan.md). M6 and the
self-healing revisions are ordered by
[implementation plan v2](history-and-recovery.md).

Judge linking ships with the canonical model in M2. Offline weight
fitting and event-site link scanning follow v1; they are not prerequisites
for M3 or M3b. The current ownership contracts are indexed in the
[design README](../reference/README.md), rather than deferred to a later work package.
