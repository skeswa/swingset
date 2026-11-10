# D-0291: Size test lock tables for concurrent imports

Recorded: 2026-11-11  
Decided by: agent, 2026-11-11; source: authorized migration validation  
Topic: Disposable test PostgreSQL settings  
Supersedes: —  
Superseded by: —

## Decision

Set `max_locks_per_transaction=512` for the disposable test/CI primary.
Keep its 2 GiB memory cap, six CPUs, data tmpfs, test concurrency and
180-second gate. The physical lifecycle profile, persistent development
database and rehearsal server keep their settings. The maintained profile
fingerprint invalidates the old disposable service only when no run owns it.

## Why

The sixteen-worker confirmation has nine PostgreSQL shared-lock-table
exhaustion errors and no cgroup OOM. A single real import holds 1,486
non-fast-path locks. Sixteen such transactions could need 23,776 entries,
before other connections; the default setting and 100 connections provide
a nominal 6,400. A 512 setting leaves headroom within the existing memory
cap. This fixes test capacity; it does not establish a faster core run.

## Links

[Measurement and validation plan](../../docs/plans/postgres-migration/7-stage-2.md#size-the-disposable-test-lock-table-for-concurrent-imports).
