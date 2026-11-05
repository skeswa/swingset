# D-0277: Resume recovery from database-bound progress

Recorded: 2026-11-05  
Decided by: agent under D-0172, 2026-11-05; source: the owner's instruction to start stage 2  
Topic: Crash recovery and bounded work  
Supersedes: A restore must finish all rebuilding in one 540-second attempt  
Superseded by: —

## Decision

Restore may install and audit bytes first while leaving `RESTORE_PENDING` set.
Before replay, reconcile the public head through the restored database's explicit
rehearsal policy. Run recovery in bounded turns without increasing unit deadlines.
Record bootstrap and source-proof progress in new operational run rows, bound
to the fresh destination UUID, original history, inputs and runtime. Commit each
verified source chunk, its equivalences and its cursor together. Never invalidate
already rebuilt caches merely because another turn starts.

Only complete replay, verification and the full history audit may remove the
restore barrier. Budget expiry, termination, changed inputs or a failed check
leave ordinary readers and workers fenced. Retained original run and generation
receipts are never rewritten as progress records.

## Why

A full recovery is expected to take hours. The sampled one-turn path stopped
after nine minutes and reinitialized caches on retry. A file-only cursor could
outlive a database rollback and skip necessary work. Database-bound progress
keeps its commit order with the data and makes interruption resumable without
weakening the source proof or promoting an incomplete restore.

## Links

[Recovery implementation](../../src/swingset/backup/activation.py).
