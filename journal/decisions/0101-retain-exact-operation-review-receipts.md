# D-0101: Retain exact operation review receipts

Status: Proposed  
Recorded: 2026-09-17  
Accepted: —  
Acceptance source: —  
Topic: Retained operating evidence  
Supersedes: —  
Superseded by: —

## Decision

Explicitly track the three immutable `review.json` receipts created for the
DCN origin runner, its actual acquisition, and schema-29 packet 004. Use
`jj file track --include-ignored` on those exact files. Leave their bytes and
the existing exclusion for regenerable review packets unchanged.

## Why

The existing broad filename exclusion also matches these operating audits.
They are retained evidence, and the packet's closed review receipt binds one
by hash. They must remain present in source freezes and future repository
copies. Tracking those files preserves the evidence without changing its
contents or widening unrelated generated-file retention.

## Links

- [DCN acquisition](../investigations/2026/dcn-origin-runner-2026-09-17.md)
- [Schema-29 rehearsals](../investigations/2026/schema29-successor-rehearsals-2026-09-17.md)
