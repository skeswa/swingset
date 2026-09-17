# D-0086: Seal successor rehearsals for the schema 28 checkpoint

Status: Proposed  
Recorded: 2026-09-17  
Accepted: —  
Acceptance source: —  
Topic: Recovery and runtime operations  
Supersedes: —  
Superseded by: —

## Decision

Build a separate packet from the exact reviewed migration, restore and input
rehearsal helpers. Bind it to checkpoint `extension28-held-20260917-002`, its
acknowledged archive commit, and one complete frozen schema 29 source inventory.
Change only explicit schema, identity and preservation seams. Keep the original
helpers and their historical evidence intact.

Migration, operational restore and input replay remain separate commands and
receipts. All 116 predecessor application tables must survive migration and
scratch preparation exactly, except the existing schema metadata exclusion.
Normal restore activation may advance only the singleton scheduling epoch once
to invalidate old hints; all other rows remain exact. Subsequent migration is
compared against that explicitly verified restored state.
After input acceptance, protect paid requests, host spacing, acquisition turns
and original immutable event history while allowing ordinary interpretation to
create valid new outputs. Keep the hold throughout. An operational restore must
verify the actual remote public baseline before clearing its restore barrier.

## Why

The previous helpers assume a schema 14 checkpoint; changing their invocation
does not make them valid for the actual schema 28 production state. Reusing their
reviewed logic avoids another independently implemented recovery protocol.
Separate packet and runtime pins allow the coordinator to review a helper after
the runtime freeze without changing the frozen runtime inventory.

This is an implementation choice awaiting operational review, not owner stage
acceptance, deployment, publication or permission to activate automatic repairs.

## Links

- [Scope, validation and invocation](../investigations/2026/schema29-successor-rehearsals-2026-09-17.md).
- [Checkpoint receipt](../evidence/runtime/schema28-checkpoint-2026-09-17/production-002/receipt.json).
