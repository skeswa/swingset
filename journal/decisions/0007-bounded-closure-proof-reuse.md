# D-0007: Reuse closure proof only after rechecking its exact evidence

Recorded: 2026-09-16  
Decided by: agent  
Topic: Build completion  
Supersedes: —  
Superseded by: —

## Decision

Within one build-completion transaction, retain a compact proof after the first
full closure validation. At the next two validation boundaries, hash the exact
database evidence again. Reuse the proof only when both the supplied manifest
and database evidence match. Otherwise perform full validation again.

The proof holds identifiers and digests, not parsed source payloads. It is
bound to one connection and one operation and discarded on exit. Dependency
arrays are hashed in bounded chunks. Ordinary validation remains independent.

## Why

The [investigation](../investigations/2026/h16-validation-investigation-2026-09-15.md)
measured 34.78 seconds for three isolated validations, before other completion
work. Repeated graph traversal and JSON decoding leave too little margin for
the existing 45-second transaction bound.

The evidence check covers selected derivation receipts and dependency bytes,
all selected source generations including unaccepted ones, accepted-decision
existence, policies, snapshot body hashes, and global revocations. It does not
infer validity from immutable triggers or a connection change counter; those
shortcuts would mishandle replacement, deletion, and savepoint rollback.

## Alternatives and limits

Raising the deadline would weaken operator-service guarantees. Removing later
checks would miss changes introduced while completing output. Retaining decoded
payloads would risk the memory failure fixed earlier. This approach preserves
all three checks and existing baseline, bundle, correction, and candidate-file
guards. Unrelated live work does not invalidate a historical closure.

Implementation and isolated timings do not establish release acceptance. A
new frozen source, replay, full build, and independent audit remain required.

## Links

- [Build contract](../../docs/reference/build.md)
- [Implementation plan](../../docs/plans/history-and-recovery.md)
