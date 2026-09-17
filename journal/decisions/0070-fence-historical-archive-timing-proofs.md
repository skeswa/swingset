# D-0070: Fence historical Archive timing proofs

Status: Proposed  
Recorded: 2026-09-17  
Accepted: —  
Acceptance source: —  
Topic: Event completion timing  
Supersedes: D-0066 for the local Archive proof boundary only  
Superseded by: —

## Decision

Add a local schema-29 dispatch dependency fence and a bounded, immutable handoff
from already checked historical Archive offers to the timing observer. Keep
origin, interpretation, whole-event and legacy eligible time unknown. No new
acquisition, year acceptance, page-kind enforcement or runtime activation follows
from timing proof.

Proof binds the exact connection, run, original watch, capture URL, history floor,
dependency revision and earliest applicable retry deadline. The producer inspects
at most 64 pending parse units and emits at most 256 observed-watch proofs. Larger
or malformed populations remain unknown. Consumers read the fence and ordinary
gates; they do not rerun candidate, capture, parent or year-inventory scans.

## Why

[D-0066](0066-require-complete-dispatch-fences-for-historical-timing.md) found that
the existing event token omitted dispatch inputs and time-only parse retries.
A separate monotonic revision covers semantic inserts, updates and deletes. The
existing control revision is checked separately so the restricted control writer
needs no broader authority. Checking the earliest pending parse retry supplies
the boundary that a revision alone cannot represent.

Global invalidation is intentionally conservative. A source-specific fence would
need to reproduce cross-source year inventory and derivation dependencies and
could miss newly relevant parents or mappings. Recomputing complete candidate
queries for every timing sample would violate the bounded observer contract.

## Consequences

Changes can invalidate unrelated offers until the next ordinary offer pass.
A changed closing fence makes the crossing interval unknown, while the dispatcher
closes timing before its scheduling mutations. Neither failure nor invalidation
resets qualified successful-progress waiting. Proofs are process-local and never
restore as positive authority. Existing collection gates and request limits are
unchanged. This local migration is outside frozen production runtime 003 and
requires its own validation, review and rollout before deployment.

## Links

- [Timing contract](../../docs/reference/event-timing.md)
- [Implementation and checks](../investigations/2026/historical-archive-timing-2026-09-17.md)
