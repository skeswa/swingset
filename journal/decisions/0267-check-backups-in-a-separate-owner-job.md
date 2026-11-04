# D-0267: Check backups in a separate owner job

Recorded: 2026-11-04  
Decided by: agent under D-0172, 2026-11-04; source: the owner's instruction to implement stage 1  
Topic: Backup credentials and verification  
Supersedes: —  
Superseded by: —

## Decision

Check every backup by restoring it into a private Postgres database and
comparing schema, constraints and history hashes before recording the set.
A separate owner job holds the scratch administration credential. Loop
containers submit a shared-volume path and manifest digest over a private
Unix socket; they receive a verification result and never the credential.
The checker accepts paths only below its configured backup directory.
Weekly drills use that same job to restore and reproduce recorded release
artifacts. The job owns expanded files as well as its scratch database;
exact directory markers and database comments fence cleanup after a crash.
The private `.owners` ledger and singleton lock stay on the shared backup
volume. Process start stamps distinguish reused PIDs. Across a replaced
container, cleanup requires the same lock and a proven former namespace
init: Linux kills its namespace's remaining processes when init dies
([kernel interface](https://man7.org/linux/man-pages/man7/pid_namespaces.7.html)).

## Why

A compressed-file digest does not prove that rows satisfy foreign keys.
Creating and auditing a fresh database needs privileges the worker must not
have. A missing checker or a failed check leaves the set unrecorded and
prevents publication. Scratch databases carry exact ownership records and
are removed after the check; crash cleanup does not prune other owners.

## Links

[One snapshot and checked backups](../../docs/plans/postgres-migration/4-hosting.md#backups-and-recovery).
Deployment wires the checker socket and shared directory at the planned
switch; stage 1 exercises the protocol locally.
