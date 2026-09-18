# D-0004: Port to PostgreSQL before moving the worker to Dokploy

Recorded: 2026-09-15; imported from the 2026-09-14 migration specification  
Decided by: owner, —; original acceptance date and approver not established by this review  
Topic: Hosting  
Supersedes: —  
Superseded by: —

## Decision

The existing migration specification targets PostgreSQL and one worker managed
by Dokploy. Port and test database behavior, validate an import from a complete
checkpoint, and rehearse recovery before transferring production. Preserve
operating holds during transfer.

This records the planned destination. SQLite on OrbStack remains the last
recorded production setup. This record authorizes no deployment.

## Why

The hosting investigation examined moving the worker to the owner's server.
Changing databases also affects transactions, saved evidence, backups, and
recovery. A copied database or a running container alone does not prove that
the system preserves those behaviors.

## Alternatives

The research considered retaining SQLite with a single persistent worker.
It also considered generic migration tools; the selected plan uses a dedicated,
validated importer. Ongoing dual writes are excluded to avoid maintaining two
competing production states. See the research for the original tradeoffs.

## Consequences

The move requires database-port work and a full-size rehearsal before cutover.
The database and evidence files must still be backed up together. A successful
held migration does not resume collection or publish a new dataset.

## Links

- [Migration plan](../../docs/plans/postgres-migration/README.md)
- [Hosting investigation](../investigations/2026/dokploy-migration-2026-09-14.md)
- [PostgreSQL experiments](../investigations/2026/postgres-migration-plan-2026-09-14.md)
- [Current status](../../docs/status.md)
