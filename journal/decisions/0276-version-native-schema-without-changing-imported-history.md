# D-0276: Version the native schema without changing imported history

Recorded: 2026-11-05  
Decided by: agent under D-0172, 2026-11-05; source: the owner's instruction to start stage 2  
Topic: PostgreSQL migrations and exact import  
Supersedes: The port's assumption that source and native schema versions advance together  
Superseded by: —

## Decision

Keep the SQLite import contract at schema 37, including the exact imported
`ops.meta.schema_version` value. Version PostgreSQL's own migrations separately
in its checked schema metadata and expose that native version through
`ops.schema_identity()`. Apply native migration 38 to admit the stored `dryrun`
environment. Never rewrite migration 37 or change an existing database identity.
An older application must refuse the newer native schema before writing.

## Why

Stage 2 requires a distinct, immutable rehearsal identity. Stage 1's native
schema allowed only `dev` and `production`. Rewriting the applied migration
would invalidate its checksums and retained recovery images. Updating the
imported source version would break the exact typed audit of the 141 source
tables. Separate versions preserve both guarantees and allow forward-only
native changes without inventing changes to SQLite history.

## Links

[Schema version semantics](../../docs/reference/schema-history.md#version-semantics).
