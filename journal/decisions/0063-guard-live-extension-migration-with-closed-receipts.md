# D-0063: Guard live extension migration with closed receipts

Status: Proposed  
Recorded: 2026-09-17  
Accepted: —  
Acceptance source: —  
Topic: Extension deployment and migration  
Supersedes: —  
Superseded by: —

## Decision

Prepare a separate helper for the reviewed schema-14→28 production migration.
Its default is read-only preflight. Execution requires the reviewed new system
to be both active and persistent, the operator hold to remain present, and all
six ordinary service/timer units to remain inactive. Only the coordinator may
execute it after reviewing its separately pinned bytes and gate.

The gate binds the frozen runtime and complete source inventory, a fresh
acknowledged schema-14 checkpoint, closed successful migration/restore rehearsals,
closed full-suite/Ruff/mypy results with separately hashed logs, and reviewed
service and external-file bindings. A receipt hash without a successful result
does not pass. Preflight before deployment explicitly expects the old system;
that phase cannot execute a migration.

Execution owns the writer and control locks before comparing all 71 predecessor
tables against the checkpoint. Only `meta.schema_version` is excluded from table
hashing; it is checked separately. Retained files, baseline, holds, controls and
host usage must match. Migration uses the pinned runtime's migration function
without accepting runtime inputs or starting pipeline work. It verifies the
complete resulting table receipts against the rehearsal and rechecks after
reopening. Unknown legacy spacing remains blocked for separate reconciliation.

## Why

The existing WP16 helper permits only schema 14→15. Reusing it would obscure the
extension's larger migration and its preservation requirements. A separately
pinned helper can use the already reviewed frozen table/source verifiers while
requiring the actual upstream results needed for this rollout.

Deployment/publication authority already exists. This guard implements that
authority; it does not invent another owner-approval gate or accept unfinished
validation. If a migration fails after one version commits, the helper reports
failure and leaves the resulting schema unknown until coordinator inspection.
It never claims an automatic rollback.

## Consequences

No fetching, derivation, publication, runtime-input acceptance, spacing-baseline
adoption, worker activation or system deployment occurs in this helper. Exact
checkpoint consistency can require a new backup and rehearsals after legitimate
state changes. That is intentional: historical evidence cannot silently authorize
new live bytes. The helper is prepared and locally tested; production execution
and stage acceptance remain separate recorded outcomes.

## Links

- [Helper](../tools/runtime/accept_event_extension.py)
- [Offline guard tests](../../tests/test_event_extension_live_migration.py)
- [Gate contract and local evidence](../investigations/2026/event-extension-live-migration-helper-2026-09-17.md)
- [Recovery plan](../../docs/plans/recovery/README.md)
- [D-0056 spacing recovery](0056-anchor-host-spacing-to-request-completion.md)
