# D-0076: Checkpoint held schema 28 without accepting inputs

Recorded: 2026-09-17  
Decided by: agent  
Topic: Recovery operations  
Supersedes: —  
Superseded by: —

## Decision

Use a separately reviewed exact schema-28 checkpoint helper bound to deployed
runtime 003 and its active system. Preserve the existing accepted input bundle,
public baseline and operator hold while recording the migrated state and paid
fixture usage. Verify the checkpoint locally and its private remote manifest
before treating it as a rollout recovery point. This grants no input acceptance,
collection, schema migration or public publication authority.

## Why

The earlier checkpoint helper is pinned to the H16 schema-14 runtime. Reusing it
with new schema assumptions would obscure its reviewed scope. Ordinary backup
CLI initialization also accepts its current inputs, which are still undergoing
disposable replay. The new helper uses the runtime's checkpoint implementation
through a read-only database connection, owns both writer and control locks,
and verifies no live database writes. Its exact-source guards reject changed
inventories, schemas, accepted inputs, systems, public baseline or active jobs.

## Links

- [New helper](../tools/runtime/checkpoint_extension28_20260917.py)
- [Current operating handoff](../investigations/2026/event-extension-operating-handoff-2026-09-17.md)
