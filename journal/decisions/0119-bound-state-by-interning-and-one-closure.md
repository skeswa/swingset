# D-0119: Bound state by interning payloads and one reachability closure

Recorded: 2026-09-17  
Decided by: owner, 2026-09-17, Sandile Keswa; source: Owner instruction in the plan review session to replace the D-0117 design with this one  
Topic: State retention and recovery cost  
Supersedes: [D-0117](0117-bound-operational-state-with-durable-archives.md)  
Superseded by: —

## Decision

Treat a generation as three parts with different lifetimes: permanent
identity (header, dependency set, predecessor, row references), output
residency (payload bytes, local or archived with two replicas), and current
materialization (scope pointers, owned by the pipeline). Retention never
deletes identity and never moves a pointer. It decides only where payload
bytes live.

Bound the live database in four gated stages, measuring before designing:

1. Attribute present storage on a disposable copy, including the ratio of
   distinct payload digests to generation rows.
2. Intern derivation payloads by digest behind a view that keeps existing
   readers unchanged, with one narrow output writer shared by completion and
   restoration.
3. Derive two sets from one dependency walk that shares the release
   closure's edge decoding but not its one-generation-per-scope selector:
   durable retention, which is every labeled generation, and local residency,
   which is everything reachable from a root. Roots are the baseline, pending
   candidates, current pointers, open findings, holds, recovery markers, and
   a recent window; older release closures stay as permanent proof but do not
   pin data locally. The window selects roots and never replaces the walk. A
   new root is accepted only when its closure is already local. Plan
   deterministically; apply under the writer and control locks, held through
   file removal, with an operation record keyed by the plan digest. Reclaim
   file space separately with an in-place `VACUUM` under the same locks,
   since row deletion only frees pages to SQLite's free list.
4. Archive generations to a checkpoint-excluded directory and two independent
   object stores, record an immutable generation-to-object mapping and replica
   locators before eviction, evict payloads only after both copies verify, and
   restore through the shared writer, only if the measured numbers still
   exceed the accepted cap.

Acceptance covers the plan direction only. It does not authorize deletion,
deployment, or a change to current retention rules. The
[plan](../../docs/plans/bounded-state-and-archive.md) owns the stages and gates.

## Why

The D-0117 design specified a history store, a root registry, a new generation
manifest, chunked and delta encodings, a seven-limit envelope, and eight work
packages before any per-table measurement existed. A review on 2026-09-17 found
that most of that machinery duplicates what the code already has: interned
manifests in `derivation_dependency_sets`, content-addressed blobs, the
checkpoint file closure, the collector, doctor, and operator pauses.

The growth source named in D-0117 is one column: every generation stores a
full copy of each output row's JSON. Interning that column by digest is one
migration and delivers what chunk sharing and deltas were meant to deliver,
with no chain depth and no second store. The D-0117 design also had a
contradiction: it promised offline checkpoint restore while moving payloads out
of the checkpoint. This design states which closure a checkpoint carries.

A second review the same day found five defects in the first draft of this
design and shaped the final form: reachability must follow dependency sets,
not only predecessors and a window; archived objects under `blobs/` would be
pinned by the file closure and copied into every checkpoint; digest comparison
alone cannot fence a root added after the comparison; one SQLite transaction
cannot cover file removal, so retries need an operation record; and the
completion writer rejects historical inputs, so restoration needs a shared
narrow writer. Making generation identity permanent removes the foreign-key
problem at any window boundary and keeps completion free of historical
branches.

A third review found five more gaps: unreachable is not the same as unknown,
so every labeled generation is durably retained and only ownerless records
are unknown; file removal must stay under the locks; a hold cannot claim
archived data, so root admission checks residency first; the release
selector's one-generation-per-scope rule cannot serve a retention window; and
restore needs a generation-to-object mapping, not only replica rows.

A fourth review found two more: treating every retained release closure as a
root would pin every released result locally forever, so only the baseline
and pending candidates pin locally and older releases are rebuilt through
restore; and deleting rows leaves file bytes unchanged, verified against
SQLite's free-list behavior, so the plan measures logical and file bytes
separately and adds an explicit reclaim step.

A fifth review rejected the first reclaim draft, which copied the database
with `VACUUM INTO` and swapped it in through the restore path. That path
installs a whole checkpoint and is not a file-replacement helper, and the
draft left replacement durability, open connections, and WAL handling
unspecified. In-place `VACUUM` under the existing locks needs no second
installation workflow and gets atomicity from SQLite's own journal.

Unverified: whether generation rows repeat across generations often enough for
interning to matter. Stage 1 measures this before stage 2 runs.

## Alternatives

- Keep the D-0117 design. Rejected: more concepts, a second source of truth for
  roots, and no measurement before design.
- Intern payloads and stop. Rejected as the whole plan because it does not
  bound history count, only bytes per copy. It stays as stage 2.
- A database-engine migration alone. Rejected: the logical model is unchanged.
- Age-based deletion. Rejected: it cannot account for publications, holds, or
  open findings.

## Consequences

Fewer moving parts: one traversal, one collector, one narrow output writer,
two policy limits. Stage 2 is reversible until its final drop. Header and
reference tables still grow, at a few hundred bytes per row, and stage 1 says
whether that matters. Apply holds the writer and control locks for the final
check and the transaction, so hold creation and candidate admission wait
during an apply. Two independent object stores are still not real, so stage 4
waits on both.

## Links

- [Implementation plan](../../docs/plans/bounded-state-and-archive.md)
- [Replaced design](0117-bound-operational-state-with-durable-archives.md)
- [State contract](../../docs/reference/state.md)
- [Large-file archive decision](0106-archive-large-files-and-scrub-local-history.md)
- [Object storage investigation](../investigations/2026/dokploy-object-storage-2026-09-14.md)
