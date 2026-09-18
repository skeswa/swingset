# D-0049: Rehearse the current schema against the published checkpoint

Recorded: 2026-09-17  
Decided by: agent  
Topic: Migration validation  
Supersedes: —  
Superseded by: —

## Decision

Use a new source-bound migration rehearsal for the integrated runtime. Verify
the actual schema-14 checkpoint, copy its database to disposable state, and
compare every existing application table before and after migration. Only the
schema-version metadata may change. Check integrity, foreign keys, reopening,
source hashes and the checkpoint database hash. Retain the operator hold.

## Why

The frozen WP16 acceptance helper targets schema 15. Reusing it as a migration
driver for the later extension would misstate its validation. A database-only
rehearsal isolates migration effects from input acceptance, replay and artifact
recovery. Those operations need separate evidence before production activation.

Independent review identified source-inventory and checkpoint-race weaknesses;
the helper now checks exact source closure and rechecks input hashes afterward.
Tests cover protected data changes, schema-only metadata changes, omitted code,
escaping paths and changed source bytes. Passing those tests is not a production
migration or an operational restore drill.

## Links

- [Rehearsal helper](../tools/runtime/rehearse_extension_migration.py)
- [Continuation evidence](../investigations/2026/v2-continuation-2026-09-17.md)
- [Migration contract](../../docs/plans/recovery/README.md#migration-enforcement-and-recovery)
