# D-0288: Bind source and accepted checkpoints separately

Recorded: 2026-11-10  
Decided by: agent, 2026-11-10; source: authorized stage 2 rehearsal  
Topic: Graph checkpoint proof  
Supersedes: —  
Superseded by: —

## Decision

Preserve the ordinary checkpoint hash and every field it covers. Migration
graph receipts bind both the unchanged source and the checkpoint after input
acceptance. Independently predict supported code-input acceptance through
read-only row projections: clear only the watches invalidation names, and
register exactly the known missing scopes with NULL materialized pointers.
Hash every other field unchanged. Actual accepted identity and context must
match the prediction. Unsupported changes cannot supply this stronger proof.

## Why

The source preseal and native-40 candidate hashes differ after acceptance.
Extraction code changes clear watch versions; registration can add NULL
scopes. Both are covered by the existing hash. Equating the two checkpoints
would reject valid acceptance; excluding those tables would hide real edits.
The server hash explanation still needs its separate read-only measurement.

## Links

[Stage 2 plan](../../docs/plans/postgres-migration/7-stage-2.md).
