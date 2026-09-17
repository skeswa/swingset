# D-0117: Bound operational state with durable archives

Status: Superseded by D-0119  
Recorded: 2026-09-17  
Accepted: —  
Acceptance source: —  
Topic: State retention and recovery cost  
Supersedes: —  
Superseded by: [D-0119](0119-bound-state-by-interning-and-one-closure.md)

## Decision

Separate current operational state from durable immutable history. Bound the
live database and local recovery window with explicit operating limits. Preserve
irreplaceable source evidence, accepted decisions, published proof, and pinned
recovery state in replicated content-addressed archives. Remove derived history
from operational storage only through verified root reachability, archive
replication, scratch reconstruction, and a restartable compaction receipt.

This direction was replaced by D-0119 on 2026-09-17 after review. It never
changed current retention rules or authorized deletion.

## Why

The operational database currently retains overlapping canonical state, full
derivation generations, append-only identity history, and operating records.
Broad invalidation can add another complete materialization. The resulting
logical history grows without a retention boundary and is copied into each full
checkpoint.

Retaining every unique input cannot have a fixed total cost. Bounded production
state plus deduplicated durable archives gives predictable operating cost while
preserving the evidence needed for audit, publication, and recovery.

## Alternatives

- A database-engine migration alone retains the same unbounded logical model.
- `VACUUM` reclaims deleted pages but cannot remove live retained history.
- Age-only deletion cannot account for publications, holds, candidates, or
  recovery dependencies.
- Keeping every full generation in the live database preserves history but does
  not provide a maintainable storage bound.

## Consequences

The system needs explicit retention roots, versioned manifests, replicated
archive storage, reconstruction tools, and compaction failure tests. Operations
gain a fixed live-state envelope and explainable reclaimable storage. Archive
capacity continues to grow with genuinely new retained evidence.

## Links

- [Replacing decision](0119-bound-state-by-interning-and-one-closure.md)
- [Implementation plan](../../docs/plans/bounded-state-and-archive.md), now following D-0119
- [State contract](../../docs/reference/state.md)
- [Recovery rollout](../../docs/plans/recovery/README.md)
- [Large-file archive decision](0106-archive-large-files-and-scrub-local-history.md)
