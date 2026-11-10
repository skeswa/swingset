# D-0289: Copy owned templates without per-block WAL

Recorded: 2026-11-11  
Decided by: agent, 2026-11-11; source: authorized stage 2 rehearsal  
Topic: Private Postgres clone strategy  
Supersedes: —  
Superseded by: [D-0290](0290-allow-twenty-minutes-for-owned-physical-clones.md), physical clone ceiling only

## Decision

Use PostgreSQL `FILE_COPY` for physical clones of the current verification
owner's quiescent template. Empty `template0` creation keeps the default
strategy. Keep the 600-second statement limit, ten-second lock limit,
ownership fences and resource caps from D-0287.

## Why

The first physical clone of the roughly 40 GB owned template reaches its
600-second limit with no unit results. PostgreSQL reports I/O and WAL waits,
then cancellation. Its default `WAL_LOG` strategy logs every copied block.
`FILE_COPY` reduces this WAL but forces cluster checkpoints before and after
creation. This tradeoff applies to the isolated scratch-copy path; it does
not change the application write deadline or authorize a failed job's retry.

## Links

[Measured failure and selected validation](../../docs/plans/postgres-migration/7-stage-2.md#measure-the-large-physical-clone-strategy).
