# D-0285: Read verified native-38 checkpoints without migrating them

Recorded: 2026-11-10  
Decided by: agent, 2026-11-10; source: D-0172  
Topic: Verification checkpoint compatibility  
Supersedes: —  
Superseded by: —

## Decision

The copy verifier and acceptance profiler may explicitly open a native-38
checkpoint through a read-only connection without a write lock. The stored
import version must be 37 and its migration checksum must match the exact
37/38 prefix and unchanged grant fingerprint. This exception admits only
native 38. Copies receive the current schema and their own dev identity.

Ordinary database opens keep requiring the current native version and full
checksum. Opening a checkpoint never migrates or writes it. A future schema
with a different row layout must make a separate compatibility decision.

## Why

The retained rehearsal base is immutable native 38. Migration 39 changes
trigger execution, with no table or column changes. An explicit checked
reader keeps that source unchanged while testing the current schema on a
new owned copy. This preserves the strict worker-open rule in D-0276.

## Links

[Stage 2 measurement and implementation gate](../../docs/plans/postgres-migration/7-stage-2.md#measured-history-fence-follow-up)
