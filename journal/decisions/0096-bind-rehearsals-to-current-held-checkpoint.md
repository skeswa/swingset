# D-0096: Bind rehearsals to the current held checkpoint

Recorded: 2026-09-17  
Decided by: agent  
Topic: Schema-29 recovery evidence  
Supersedes: —  
Superseded by: —

## Decision

Prepare a separate candidate-005 rehearsal packet for a newly verified schema-28
checkpoint. Require exact hashes for its manifest, capture/private-acknowledgment
receipt, verified summary and reviewed checkpoint helper. Bind the same held
source 003, published H16 baseline, accepted input bundle and 116-table
predecessor. Verify every checkpoint file before sealing the packet.

Reuse only the byte-pinned schema-28-to-29 rehearsal contract. Carry every
top-level checkpoint sidecar into preservation checks, including the DCN origin
day claim and robots provenance. Preserve packet 003 and its actual receipts.
The coordinator formats, freezes and executes; an independent reviewer checks
the new packet before any rehearsal or live rollout.

## Why

Checkpoint 002 stops at 15 paid Archive requests. Later Archive and origin
requests cannot disappear during restore. Updating a retained packet in place
would obscure which production state its successful rehearsals proved. A fresh
packet makes that boundary explicit without treating the old schema-14 migration
or WP16 helper as a generic migration driver.

This is an implementation choice under existing operating authority, not owner
year acceptance or a claim that deployment, ordinary resumption or publication
has passed its gates.

## Links

- [Standing authority](0087-authorize-remaining-v2-acquisition-and-operations.md)
- [Successor rehearsal contract](0086-seal-schema29-successor-rehearsals.md)
- [Origin accounting sidecars](0095-preserve-origin-fixture-request-claims.md)
- [Dated operations](../investigations/2026/schema29-successor-rehearsals-2026-09-17.md)
