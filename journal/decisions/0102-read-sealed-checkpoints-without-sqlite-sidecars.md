# D-0102: Read sealed checkpoints without SQLite sidecars

Recorded: 2026-09-17  
Decided by: agent  
Topic: Checkpoint inspection  
Supersedes: —  
Superseded by: —

## Decision

Use SQLite `mode=ro&immutable=1` when reading a verified, sealed checkpoint
whose database will not change. Keep ordinary read-only connections for live
or changing scratch databases, where committed WAL contents must remain visible.
Test that checkpoint inspection preserves its exact file closure.

If an inspection creates unmanifested SQLite sidecars, retain their bytes and
failure evidence before cleanup. Remove only the exact inspection-created
files after checking hashes, an empty WAL, regular singly linked files, closed
operations and no open database or sidecar descriptors. Reverify every retained
checkpoint file and its manifest before retrying in a new attempt directory.

## Why

A coordinator `mode=ro` inspection created empty WAL and shared-memory files
beside checkpoint 004. The database hash remained unchanged, but its file
closure no longer matched the manifest. Input rehearsal attempt 002 correctly
refused it before copying state. Read-only SQLite access can still create
sidecars; immutable mode is appropriate only for the sealed checkpoint.

This is cleanup of the inspection's unmanifested temporary files. It does not
permit rewriting checkpoint database bytes, manifests or retained receipts.

## Links

- [Failed preparation](../evidence/runtime/schema29-successor-rehearsals-2026-09-17/inputs-002/prepare.json)
- [Captured temporary files and cleanup](../evidence/runtime/schema29-successor-rehearsals-2026-09-17/checkpoint004-transient-cleanup-001/captured.json)
- [Successor rehearsals](../investigations/2026/schema29-successor-rehearsals-2026-09-17.md)
