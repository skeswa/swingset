# D-0265: The Postgres worker can maintain application tables

Recorded: 2026-11-04  
Decided by: agent under D-0172, 2026-11-04; source: the owner's instruction to implement stage 1  
Topic: Database permissions  
Supersedes: —  
Superseded by: —

## Decision

Grant the Postgres worker `MAINTAIN` on ordinary application tables.
Keep the environment identity table administrative. The worker still cannot
alter tables, truncate them, disable triggers, or become the owner role.
Reclaim runs under the state-exclusive lock and reports each table it
finished, so an interrupted multi-table operation can resume.

## Why

Reclaim must remain available to the worker without putting an owner's
password in its container. Postgres 17 provides a table-scoped maintenance
privilege for vacuum and analyze. Logical deletion still passes the existing
retention and eviction guards; maintenance does not bypass those guards.

## Alternatives and consequences

Owner-only reclaim would require a separate privileged operator for this
existing task. Table-scoped maintenance keeps that credential out of the
worker, but permits heavy table locks. Tests prove the grant and the denied
schema operations. Reclaim is atomic per table, not across the whole database.

## Links

[Worker roles and timeouts](../../docs/plans/postgres-migration/3-database.md#roles).
Postgres documents the [maintenance privilege](https://www.postgresql.org/docs/17/ddl-priv.html).
