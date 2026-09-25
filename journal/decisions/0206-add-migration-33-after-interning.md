# D-0206: Add migration 33 after interning

Recorded: 2026-09-25  
Decided by: agent, 2026-09-25, under D-0175; source: phase 1 of the [architecture plan](../../docs/plans/architecture.md)  
Topic: State schema  
Supersedes: in part, [D-0167](0167-intern-derivation-payloads-in-the-last-migration.md) (interning is the last migration)  
Superseded by: —

## Decision

`SCHEMA_VERSION` is 33. Migration 33 (`0033_derivation_equivalences.sql`)
runs after interning and does two things:

- It adds three tables for output cutoff: `derivation_recipes` (a recipe by
  its sha256), `derivation_equivalences` (a scope's new input fingerprint maps
  to a kept generation, with its own recipe, dependency set and run) and
  `source_equivalences` (the same for a parse, recipe inline). Each refuses
  UPDATE and DELETE, moves the history dispatch fence, and gets publication
  fences. Nothing writes them until the cutoff lands, so the migration alone
  changes no behaviour.
- It narrows `derivation_source_units_update` and
  `derivation_source_selections_update`: the `watch` and `source_selections`
  tokens move only when `watch_id`, `unit_key`, `accepted_generation_id` or
  `legacy_snapshot_id` changes. A write of `desired_fingerprint` alone,
  which every parse makes, no longer moves them.

The rows are immutable, not permanent. Invalidation step 3 collects them with
the same one-row-per-transaction trigger lift `closure_collection` uses. A
derivation equivalence stays live while its fingerprint is its scope's
`desired_fingerprint`, or while it is the newest one naming the scope's
pointer. A source equivalence stays live while its fingerprint is its unit's
`desired_fingerprint`. A `derivation_recipes` row stays live while an
equivalence names it. The rest serves only a revert and may go.

## Why

D-0167 made interning the last migration so schemas 30 and 31 could deploy
without it. Production reached schema 32 with candidate 019 on 2026-09-25, so
that reason is spent. The cutoff needs a place to record an equivalence,
because `derivation_generation_no_update` forbids amending a generation.

Recipes go in their own table because a kind's recipes are nearly identical
(29k dancers share one): a code-only deploy then writes about 10 MB instead of
about 50 MB. No derivation reads `desired_fingerprint`; the dependency reader
takes `unit_key`, `accepted_generation_id` and `legacy_snapshot_id`. Once
parse cutoff stops moving the accepted pointer, a token bump on every parse
would send each dependent project unit down the slow currentness path forever
(inventory's took about 23 s). A code-only deploy adds about 35,800
equivalences and 33,000 root dependency sets (R5: 12 MB and 10 MB before
indexes), so without a collector the rows grow with deploys, not data (I6).

## Links

- [Derivation invalidation plan, step 1](../../docs/plans/derivation-invalidation.md)
- [Schema history](../../docs/reference/schema-history.md)
