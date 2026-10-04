# D-0245: Cap the database at 50 GB and build daily after the reclaim

Recorded: 2026-10-11  
Decided by: agent, 2026-10-11, under D-0172, on the owner's go-ahead for the reclaim ("yeah lets proceed")  
Topic: Retention  
Supersedes: the 76 GB cap of [D-0233](0233-raise-the-size-cap-to-76-gb-until-phase-4.md) and the weekly builds of [D-0235](0235-build-weekly-until-phase-4-frees-room.md)  
Superseded by: —

## Decision

Production sets `retention.max_database_bytes = "50GB"` and
`build_interval_seconds = 86400`. Both earlier records ended "until phase 4
frees room"; the reclaim of 2026-10-11 did.

## Why

`gc --reclaim` took the file from 72.95 GB to 40.74 GB and left 157 GB of quota
free. Phase 4's deploy steps set the cap at the size after reclaim plus about
8 GB; 50 GB rounds that up and leaves two to three weeks at the measured
growth, with `[size-cap-near]` at 45 GB. Partitioned builds add about 4 MB
each instead of the closure path's 1.3 GB, so daily dry-run candidates cost
little and keep the release current.

## Links

- [Receipt](../evidence/runtime/reclaim-2026-10-11/receipt.json)
