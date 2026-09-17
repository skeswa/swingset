# D-0075: Reuse dancer readiness only within owned read snapshots

Status: Proposed  
Recorded: 2026-09-17  
Accepted: —  
Acceptance source: —  
Topic: Measured ordinary offline selection cost  
Supersedes: —  
Superseded by: —

## Decision

Reuse the complete dancer-cohort readiness result only inside the selector's
owned read-only snapshot. Use the existing bounded currentness cache, keyed by
the exact ordered work-unit cohort and ordinary currentness callback. Keep both
true and false results; recompute after eviction or snapshot closure.

Only the ordinary database currentness callback participates. Custom callbacks
may depend on mutable state outside SQLite and always recompute. Caller-owned
transactions, including read-only callers, get no new cohort cache. Mutable
worker transactions, consistency groups and normal admission remain unchanged.

## Why

The [measured comparison](../evidence/runtime/offline-selector-profile-2026-09-17/comparison-001/report.json)
verified the first map-currentness cache improvement: 3,009 map checks needed
only one desired calculation. Selection still timed out after 30.0075 seconds.
It repeated the same complete dancer proof scan 168 times, using 21.61 cumulative
seconds. Per-dancer fallback itself took little time; repeated full-cohort
loading dominated this measured interval.

The ordinary selector already owns a read snapshot, and these proofs depend on
that database snapshot. Reusing the answer within the same narrow lifetime
removes redundant work without changing the candidate population or ordering.
It does not authorize a worker or declare any event complete.

## Consequences

Reuse inherits the cache's connection, transaction, total-change, data-version
and `query_only` guards, its 2,048-entry limit, exception behavior and complete
cleanup at the snapshot boundary. Changed cohorts use different keys. Admission
still checks current controls and dependencies after selection closes.

A differential fixture must select the same healthy work as an uncached scan;
mutation, rollback, concurrent writes, custom callbacks, failure and eviction
checks must preserve currentness behavior. A new source-bound profile on the
same disposable scratch is required before claiming a measured service gain.
This decision records a local implementation choice, not owner stage acceptance,
deployment, production throughput or publication.

## Links

- [First measured fix](0072-profile-offline-selection-before-changing-replay.md)
- [Profile and validation evidence](../investigations/2026/offline-selector-profile-2026-09-17.md)
