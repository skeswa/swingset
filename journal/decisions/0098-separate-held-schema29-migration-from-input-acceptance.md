# D-0098: Separate held schema-29 migration from input acceptance

Status: Proposed  
Recorded: 2026-09-17  
Accepted: —  
Acceptance source: —  
Topic: Held production migration  
Supersedes: —  
Superseded by: —

## Decision

Use a dedicated, exact schema-28-to-29 production helper for frozen candidate 005. Require independent source validation, build and service-binding review,
a fresh acknowledged checkpoint, actual migration and restore rehearsals, and
bounded replay evidence. Bind their exact hashes in a reviewed execution gate.

Run a read-only preflight before activating the candidate system under the
existing hold. After activation, recheck the candidate source and system,
six inactive ordinary units, hold, controls, publication baseline, pending state
and all 116 predecessor tables and checkpoint sidecars. Migrate under the
writer and control locks; verify table preservation, schema integrity, foreign
keys, reopening and the sole new dispatch-fence table.

Do not combine this operation with production input acceptance, collection
resumption or publication. Those gates remain open, including replay routing,
interpretation findings and operating calibration. Preserve frozen candidate
005 even while a successor fixes the manual registry invalidation defect.

## Why

Schema-29 migration can be verified independently of accepting a new input
bundle or starting work. The bounded replay exposed a manual-artifact routing
defect; moving the schema under hold must not imply that the runtime has gained
operating acceptance. A dedicated helper also avoids stretching the old
schema-14 migration or WP16 operation beyond their reviewed contracts.

This engineering choice uses D-0087's existing deployment authority after
concrete gates pass. It records no new owner acceptance of a historical year,
V5/V6 completion, source-kind admission or ordinary resumption.

## Links

- [Standing authority](0087-authorize-remaining-v2-acquisition-and-operations.md)
- [Fresh checkpoint rehearsal binding](0096-bind-rehearsals-to-current-held-checkpoint.md)
- [Manual registry routing defect](0097-keep-manual-registry-artifacts-on-their-replay-path.md)
- [Operating handoff](../investigations/2026/event-extension-operating-handoff-2026-09-17.md)
