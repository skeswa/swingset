# D-0113: Seal held input acceptance on disk

Status: Proposed  
Recorded: 2026-09-17  
Accepted: —  
Acceptance source: —  
Topic: Held production input acceptance  
Supersedes: —  
Superseded by: —

## Decision

Give candidate 006 a dedicated production input-acceptance helper. Keep its
preflight read-only. Derive the exact accepted-input transition on a private,
disk-backed SQLite copy under `/var/tmp`, then require independent review of
that seal before a separate execution can change the held database.

Bind execution to the deployed source and system, postmigration audit,
checkpoint packet, rehearsed 51-row input map, publication baseline, hold and
inactive ordinary units. Hold the writer and control locks through the live
transition. Do not let this helper run workers, fetch, repair, publish, remove
the hold or resume ordinary services.

## Why

The production database is too large for an in-memory rehearsal. A disk-backed
copy keeps memory bounded while full before and expected-after hashes preserve
the independently reviewable gate. A separate helper preserves D-0098's
boundary between migration, input acceptance and ordinary operation.

This proposal records an implementation choice under D-0087. It grants no
historical-year acceptance and records no operating or publication acceptance.

## Links

- [Separate migration from input acceptance](0098-separate-held-schema29-migration-from-input-acceptance.md)
- [Runtime tool index](../tools/runtime/README.md)
- [Operating handoff](../investigations/2026/event-extension-operating-handoff-2026-09-17.md)
