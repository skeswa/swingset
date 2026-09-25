# D-0208: Keep the 64 GB cap until closure collection

Recorded: 2026-09-25  
Decided by: agent, 2026-09-25, under D-0175; source: phase 1 of the [architecture plan](../../docs/plans/architecture.md)  
Topic: Retention  
Supersedes: in part, [D-0196](0196-raise-the-database-size-cap-to-64-gb.md) (the cap comes down once the planner collects)  
Superseded by: —

## Decision

`retention.max_database_bytes` stays at 64 GB. D-0196's exit, a planner that
collects superseded generations, moves to step 3 of the derivation
invalidation plan: store the release closure once, collect what nothing
names, reclaim. The cap comes down, with its own record, after step 3's
post-check receipt. Phase 1 does not lower it.

Until then the state grows with ordinary work. `[state-size]` fires above
50 GB of the state directory without `checkpoints/`, `venv/` and
`uv-cache/`, the measure of phase 4's 55 GB trigger; the response, including
removing superseded candidates, is in the
[operation guide](../../docs/guides/operation.md#disk-space).

Equivalences are never updated, and nothing deletes them before step 3.
A code-only deploy adds about 35,800 derivation equivalences and about
39,600 root sets (about 32 MB) and no source equivalences; a `recipe/env` or
all-parse deploy also adds up to 33,900 source equivalences with recipes of
about 1 KB, about 35 MB. Step 3's collector starts, ahead of phase 2, when
`derivation_equivalences` or `source_equivalences` passes 500,000 rows,
about 13 or 14 such deploys. The watchdog's `[equivalences]` line is that
clock; `gc --plan --candidates-only` prints the counts. An earlier trigger,
`gc --plan` above 2 GB resident, is dropped: the full plan already peaks at
8.9 to 12.1 GB, so it could never fire.

## Why

Phase 1 stops per-deploy generations but frees nothing, and ordinary work
still grows the state with the paced inventory
([D-0210](0210-pace-the-inventory-like-history.md)), an estimated 1.5 to
2 GB a day (about 5 GB a day on the worker under 019). What happens first as
the cap nears is an open owner decision in the architecture plan, whose
steady-state paragraph has the arithmetic. Candidate pruning
walks no graph and frees about 5 GB once. `gc --collect-closures` frees only
proofs of failed builds. The bytes the cap guards against are the manifests
and superseded rows that only step 3 removes.

## Links

- [Derivation invalidation plan, step 3](../../docs/plans/derivation-invalidation.md#3-store-the-release-closure-once-and-collect-the-copies)
