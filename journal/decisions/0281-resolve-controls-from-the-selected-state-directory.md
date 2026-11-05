# D-0281: Resolve controls from the selected state directory

Recorded: 2026-11-09  
Decided by: agent, 2026-11-09; source: stage 2 implementation under D-0172  
Topic: Database connection identity  
Supersedes: —  
Superseded by: —

## Decision

A state directory's explicit controller URL takes precedence over
`SWINGSET_CONTROL_DATABASE_URL`, matching worker URL resolution. The environment
remains a fallback when the selected directory has no controller URL. Worker
credentials never substitute for controller credentials. The controller must
still match the worker's database endpoint and stored identity.

## Why

The private verifier inherits its base database's controller URL, then creates
private copies with their own settings. Environment-first resolution selected
the base controller for a copy. The existing destination guard correctly
refused the operation, preventing verification from continuing. A native
regression freezes and clones a database, pauses only the clone despite the
ambient source URL, and checks that the source remains unpaused.

## Links

[Database roles and connection selection](../../docs/plans/postgres-migration/3-database.md#roles).
