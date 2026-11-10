# D-0290: Allow twenty minutes for owned physical clones

Recorded: 2026-11-11  
Decided by: agent, 2026-11-11; source: authorized stage 2 rehearsal  
Topic: Private Postgres clone deadline  
Supersedes: D-0287 and D-0289, physical clone ceiling only  
Superseded by: —

## Decision

Allow 1,200 seconds for a physical clone of the verification owner's
quiescent scratch template. Keep FILE_COPY, ten seconds for empty creation
and locks, sixty seconds for application writes, the existing resource caps
and exact owned cleanup. A failed job is never replayed automatically.

## Why

The first FILE_COPY clone of a 40,033,130,163-byte template reaches the
600-second limit. During 482 seconds of active copying, its PostgreSQL
cgroup writes 31.89 GB, about 66.18 MB/s. At that rate, template bytes alone
take about 605 seconds, before checkpoints and other overhead. This measures
the job's I/O, not a host bottleneck or a completed clone time. Twenty
minutes provides a finite allowance; a fresh graph must still prove success.

## Links

[Measured failure and validation plan](../../docs/plans/postgres-migration/7-stage-2.md#bound-the-measured-file_copy-clone).
