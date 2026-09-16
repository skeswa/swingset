# Move the worker to PostgreSQL and Dokploy

This plan moves Swingset from its recorded SQLite/OrbStack setup to PostgreSQL
and a worker managed by Dokploy. The goal is a verified transfer that preserves
data, recovery behavior, and operating holds. It does not resume collection
or publish a new dataset.

Status: implementation specification, originally recorded 2026-09-14.
See [current status](../../status.md) for operating evidence and
[D-0004](../../../journal/decisions/0004-postgres-migration.md) for the imported
choice and its acceptance limits.

## Approach

Prove three things separately: the new database preserves behavior, the
checkpoint import accounts for every record and file, and the deployed worker
preserves scheduling, pauses, and recovery. Test on isolated state before
transferring the production writer. Keep one writer.

## Implementation steps

| Read when                                           | Detail                                      |
| --------------------------------------------------- | ------------------------------------------- |
| Establishing scope, interfaces, and required access | [Contracts and run inputs](contracts.md)    |
| Starting the port, M0                               | [Inventory and frozen inputs](inventory.md) |
| Implementing database behavior, M1–M2               | [Database port](database.md)                |
| Loading retained data, M3                           | [Import and independent audit](import.md)   |
| Proving recoverability, M4                          | [Checkpoints and restore](checkpoints.md)   |
| Packaging the service, M5                           | [Worker and monitoring](worker.md)          |
| Checking the whole system, M6                       | [Acceptance](acceptance.md)                 |
| Rehearsing and transferring, M7–M9                  | [Cutover and recovery](cutover.md)          |

The M numbers here name migration steps. They are separate from the original
project milestones. Each detailed page retains its original section numbers
so earlier planning references remain understandable.

## Evidence and background

- [Hosting investigation](../../../journal/investigations/2026/dokploy-migration-2026-09-14.md)
- [PostgreSQL experiments and inventory](../../../journal/investigations/2026/postgres-migration-plan-2026-09-14.md)
- [Object storage investigation](../../../journal/investigations/2026/dokploy-object-storage-2026-09-14.md)

The experiments support the plan; they do not substitute for its acceptance tests.
