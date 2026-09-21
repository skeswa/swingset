# D-0186: Raise the database size cap to 32 GB

Recorded: 2026-09-21  
Decided by: agent, under D-0183  
Topic: State retention  
Supersedes: the provisional 8,000,000,000-byte value in [D-0141](0141-two-retention-limits-and-the-collector-age-become-policy.md)  
Superseded by: —

## Decision

`[retention] max_database_bytes` is 32 GB in the shipped configuration.
The pause the cap raises is unchanged, and so is the trigger in `AGENTS.md`
for reopening storage work at 20 GB; this cap sits above that trigger so the
pipeline never stops itself before the topic is reopened by a measurement.

## Why

D-0141 called 8 GB a judgment call to be confirmed by measurement. The
schema 32 migration left the file at 9.1 GB before reclaim, and every
re-derivation after it writes new generations while keeping the old ones,
so the drain that candidate 010 restarted crossed 8 GB again at 04:45 UTC
with 13,119 parses and 13,152 projections still queued, and the pipeline
paused itself for the second time in two hours. The worker has 403 GB free.
A cap that pauses a healthy drain protects nothing; the cost of the extra
history is bounded by the retention plan when it is reopened.

## Links

- [Candidate 010 receipt](../evidence/runtime/candidate-010-2026-09-21/receipt.json)
- [Operation guide, disk space](../../docs/guides/operation.md#disk-space)
