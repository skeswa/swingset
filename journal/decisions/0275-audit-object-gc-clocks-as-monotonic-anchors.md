# D-0275: Audit ordinary storage and candidate anchors

Recorded: 2026-11-05  
Decided by: agent under D-0172, 2026-11-05; source: the owner's instruction to implement stage 1  
Topic: History backup and operational anchors  
Supersedes: Stage 1's immutable storage-clock and candidate-pointer assumptions  
Superseded by: —

## Decision

Restore every `objects` field exactly before workers. After ordinary reuse,
allow only `stored_at` to advance under the existing object writer's GC rule.
A known clock stays known and never moves backwards in C text order; an
unknown clock stays unknown. Check this against the original backup rows
with bounded native COPY and a temporary-table join. Object class, digest,
sizes and every original key remain exact. A release manifest may also
advance only its current `candidate_id` through ordinary `name_candidate`;
its digest, bytes, creation time and verdict remain exact.

## Why

The real cold rebuild reproduced all 21 release files exactly, but reusing one
release manifest advanced its storage clock by ten minutes. The existing writer
refreshes that clock to protect reused bytes from the collector's one-day age
floor. It is an operational GC anchor, not a source or processing timestamp.
Allowing arbitrary clock changes or ignoring object identity would lose the
original guarantee; changing the writer would change collection policy.
The same rerender normally names its new candidate while preserving the
manifest and original immutable build receipts.

## Links

[History row rules](../../docs/plans/postgres-migration/4-hosting.md#backups-and-recovery).
