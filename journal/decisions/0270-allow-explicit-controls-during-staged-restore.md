# D-0270: Allow explicit controls during a staged restore

Recorded: 2026-11-04  
Decided by: agent under D-0172, 2026-11-04; source: the owner's instruction to implement stage 1  
Topic: Restore and operator controls  
Supersedes: —  
Superseded by: —

## Decision

The separate control role may perform an explicit pause or resume while
`RESTORE_PENDING` is set. Its paired database UUID, authorization and B/C
locks still apply. Every change records its actor, reason and control event.
The restore job never resumes an operator's pause automatically.

Normal opens, acquisition and publication remain blocked by the restore
barrier. Rebuilding also respects operator pauses: if one prevents necessary
work, activation fails and keeps the barrier. The operator can explicitly
resume that control under the barrier, then rebuild and verify before
activation removes it. An unrelated out-of-band hold remains in place.

## Why

A paused backup must preserve that pause. Rebuilding must neither bypass it
nor become impossible to authorize: rejecting every control change under the
restore marker left no ordinary way for the operator to permit recovery.
The independent barrier keeps the staged database from serving normal work
while the explicit control action is recorded.

## Links

[Postgres restore procedure](../../docs/guides/backup-and-restore.md#postgres-migration-bookmark).
