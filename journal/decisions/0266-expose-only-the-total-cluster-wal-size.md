# D-0266: Expose only the total cluster WAL size

Recorded: 2026-11-04  
Decided by: agent under D-0172, 2026-11-04; source: the owner's instruction to implement stage 1  
Topic: Database monitoring permissions  
Supersedes: —  
Superseded by: —

## Decision

Let the worker call `ops.wal_size()` to read the physical bytes in the
Postgres cluster's WAL directory. The function has a fixed `pg_catalog`
search path and runs as the non-login schema owner, which alone gets
permission to call `pg_ls_waldir()`. The worker receives the aggregate size,
without directory entries or broad monitoring privileges.

## Why

The old watchdog measured the SQLite WAL file. Postgres owns WAL for the
whole cluster. Its cumulative bytes-written counter cannot replace a current
file-size check. Preserve the existing 4 GB alert threshold, explicitly
labelled as cluster WAL pressure, rather than reporting an invented zero.
The shared server's other databases can also contribute to this alert.

## Links

[Postgres monitoring functions](https://www.postgresql.org/docs/17/functions-admin.html#FUNCTIONS-ADMIN-GENFILE)
allow a narrow execute grant. The [migration plan](../../docs/plans/postgres-migration/3-database.md#roles)
owns the worker's other permissions.
