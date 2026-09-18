# D-0058: Rehearse the real restore protocol under hold

Recorded: 2026-09-17  
Decided by: agent  
Topic: Extension operating rehearsal  
Supersedes: —  
Superseded by: —

## Decision

Restore an exact acknowledged schema-14 checkpoint into a new disposable state
directory using the runtime's actual restore protocol. Keep its byte-exact
operator hold throughout public verification, activation and migration to
the pinned extension schema. Check the actual public head twice during restore and verify the
baseline candidate's remote manifest and file hashes. Never run workers,
accept inputs, publish, or activate repairs in this rehearsal.

Pin the separately frozen helper, runtime inventory, checkpoint manifest and
acknowledged private archive commit. The checkpoint and archive pins are
explicit arguments, together with the exact target schema, so a newer verified pre-deployment checkpoint can preserve
additional paid requests without changing the helper's authority. The coordinator
must supply pins from an actual backup acknowledgment; this helper does not
establish a new private-archive upload receipt.

## Why

A database-only migration specimen does not exercise restore barriers, public
reconciliation, installation or activation. The existing top-level restore
protocol activates the schema-14 state before opening it for migration. The
retained operator hold protects that interval and remains after migration.
Following this existing protocol avoids inventing a different restore order.

All pre-existing application tables and copied private files must remain exact,
apart from schema-version metadata. Public transport is read-only, serial,
bounded, uses the project User-Agent and five-second host spacing, and writes
its cache only inside the disposable operation directory. Ordinary production
units must be inactive before and after the rehearsal.

## Consequences

This rehearses an actual restore of production checkpoint bytes; it is not
production activation, acceptance of external inputs, a source-acquisition run,
or H18 acceptance with outstanding extension work. Existing offline extension
restore tests cover additional state absent from the older production schema.
An operational receipt is written only by the coordinator's later execution.

## Links

- [Restore contract](../../docs/reference/operations.md#backup-and-restore)
- [Prepared helper and validation](../investigations/2026/extension-restore-rehearsal-preparation-2026-09-17.md)
- [Current continuation receipts](../investigations/2026/v2-continuation-2026-09-17.md)
