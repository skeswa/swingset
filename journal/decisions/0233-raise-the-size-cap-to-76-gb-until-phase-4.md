# D-0233: Raise the size cap to 76 GB until phase 4 frees room

Recorded: 2026-10-03  
Decided by: owner, 2026-10-02 (D-0228's fallback, "I like the rec"); applied by agent, 2026-10-03  
Topic: Retention  
Supersedes: the 72 GB cap of [D-0220](0220-raise-the-cap-to-72-gb-for-the-phase-1-switch-only.md), in production  
Superseded by: [D-0245](0245-cap-the-database-at-50-gb-and-build-daily-after-the-reclaim.md)

## Decision

`retention.max_database_bytes` is 76 GB in production until phase 4's
reclaim brings the file back under 72 GB; a record after it sets its own
value. Phase 4's switch alone does not: its move and collection leave the
file about 73 GB with about 35 GB of free pages, and reclaim may wait for
room until the old checkpoints are removed (phase 4 deploy steps 6 and 9).
No further raise without the owner: the quota had 76.9 GB free.

## Why

The first build under phase 2 switch 2a (candidate `cand_2e3cfa53cc07470f`,
2026-10-03 04:24 UTC) took the file from 71.34 to 72.66 GB, and the
next cycle paused at 04:24:12 as over the cap. D-0228 named this raise as
the fallback, and the 2a reviewer's condition C3 asked for it before a
pause. Each build adds about 1.3 GB until phase 4 collects what builds
leave, so with 24-hour builds this gives about two days.

## Links

- [Receipt](../evidence/runtime/architecture-phase2a-2026-10-02/receipt.json)
