# D-0066: Require complete dispatch fences for historical timing

Status: Proposed  
Recorded: 2026-09-17  
Accepted: —  
Acceptance source: —  
Topic: Event completion timing  
Supersedes: —  
Superseded by: D-0070 for the local Archive proof boundary only

## Decision

Keep historical Archive and origin eligible time unknown until a bounded proof
covers every dispatch dependency and time boundary. Do not treat an offer that
passed its checks earlier in a cycle as proof for a later interval. This audit
adds no positive timing authority, schema migration, request, or acquisition gate.
The requirement remains open.

## Why

The current event measurement token fences enumerations, admitted support and
many snapshot changes. Archive dispatch also depends on capture selection,
current event mappings and year acceptance, parent readiness, and pending parse
retry state. These are not all fenced by that token. A retained capture can
change from incomplete to waiting when a retry becomes due, without any database
write or measurement-token change. A short arbitrary expiration cannot replace
that exact time boundary.

Origin dispatch additionally requires the current run's intent, retained
archive-first proposal and per-event cadence. Its proposal scans retained source
captures and archive queries. Repeating those scans in every timing sample would
violate the observer's bounded-work contract.

## Consequences

Ordinary acquisition timing stays implemented. Historical, interpretation and
whole-event eligible age remain unimplemented. Before extending the observer,
provide bounded dependency invalidation and the earliest applicable retry
boundary, and bind the exact offer, selected capture, original request, source
kind, year and current run. Missing or stale proof must remain unknown. These
engineering requirements create no new owner-approval gate.

## Links

- [Detailed audit](../investigations/2026/historical-event-timing-boundary-2026-09-17.md)
- [Current timing contract](../../docs/reference/event-timing.md)
- [Extension scheduling contract](../../docs/reference/scheduling.md)
