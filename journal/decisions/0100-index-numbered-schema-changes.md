# D-0100: Index numbered schema changes

Recorded: 2026-09-17  
Decided by: agent  
Topic: State documentation  
Supersedes: —  
Superseded by: —

## Decision

Add a concise schema history with one entry for each numbered SQLite migration.
Link each entry to its SQL and link the index from the local state reference.
Keep current deployment and publication claims in the status page.

## Why

The owner asked where to find what changes in each schema version. The existing
state reference explains current behavior but does not give a complete ordered
list. An index makes the migration history readable without duplicating the SQL
or confusing a database version with an accepted or published dataset.

## Links

- [Schema history](../../docs/reference/schema-history.md)
- [Local state](../../docs/reference/state.md)
- [Current status](../../docs/status.md)
