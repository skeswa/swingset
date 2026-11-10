# D-0287: Bound owned physical clones separately

Recorded: 2026-11-10  
Decided by: agent, 2026-11-10; source: authorized stage 2 rehearsal  
Topic: Private Postgres clone deadline  
Supersedes: —  
Superseded by: [D-0290](0290-allow-twenty-minutes-for-owned-physical-clones.md), physical clone ceiling only

## Decision

Allow up to 600 seconds to create a database from this verification owner’s
quiescent scratch template. Keep a ten-second lock timeout and ten seconds
for empty `template0` creation. Foreign templates are refused before a new
claim. Application writes retain their sixty-second limit; resource caps,
operation ceilings and exact owned cleanup stay unchanged.

## Why

The roughly 39 GB native-40 copy accepts current inputs. Its first physical
shard clone then fails: the initial receipt and Postgres statement timeout
are 10.285 seconds apart, before any clone completes. Code order strongly
identifies the old ten-second create limit; statement text was not logged.
A physical copy needs a separate finite I/O allowance. The new limit is a
ceiling, not a measured completion time or permission to retry a failed job.

## Links

[Failed graph receipt](../evidence/runtime/postgres-stage2-2026-11-10/native40-whole-graph-failed.json).
