# D-0278: Bootstrap privileged table functions outside the application

Recorded: 2026-11-05  
Decided by: agent under D-0172, 2026-11-05; source: the owner's instruction to start stage 2  
Topic: PostgreSQL bootstrap privileges  
Supersedes: The assumption that the initial native schema needs only its owner login  
Superseded by: —

## Decision

A trusted platform administrator prepares only the frozen migration-37 table
phase on a fresh database. It includes the internal byte-window function that
PostgreSQL permits only an administrator to define. Preparation assigns the
application owner and records a marker bound to the database, exact SQL, roles
and empty catalog. The limited migration login adopts only that exact prepared
phase and finishes constraints, triggers and identity. Any drift or data
refuses adoption. Workers cannot prepare or adopt it.

Shared-cluster administrator credentials stay outside application and migration
containers. The backup checker's administrator belongs only to its private
scratch cluster. Existing migration SQL is not rewritten or selectively skipped.
The owner receives only execute permission for the cluster WAL listing used by
its aggregate size function, with no grant authority. Workers can read the
aggregate, not the raw listing. Unexpected existing grant authority refuses
bootstrap instead of changing another administrator's grant.

## Why

A real login test failed on `LANGUAGE internal`; a same-session role-switch
test had hidden part of the bootstrap boundary. Giving the application a shared
superuser would expose other projects. The checked initial phase handles the
one privileged definition while preserving ordinary ownership and exact import.

## Links

[Checked bootstrap](../../src/swingset/state/postgres_bootstrap.py).
