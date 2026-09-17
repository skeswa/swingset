# D-0026: Record stage outputs and verified event progress separately

Status: Proposed  
Recorded: 2026-09-16  
Accepted: —  
Acceptance source: The owner authorized continued history and recovery implementation. This local extension has not received operating acceptance.  
Topic: Event progress  
Supersedes: —  
Superseded by: —

## Decision

Record compact successful acquisition and interpretation facts in the same
transaction as the retained snapshot or final accepted decision. Deduplicate
the output identity. Failed requests, issuance, unchanged polling without new
evidence, and repeated verification do not create successful output facts.
Do not backfill historical success timestamps.

A bounded observer qualifies event progress separately. Require a verified
missing baseline for a normalized request and stage, the same enumeration, a
later successful operation, and exact positive verification that the operation
supplied the missing evidence. Record operation time and verification time
separately. A first observation of existing evidence establishes availability,
not a newly successful operation. Restoring or rechecking old files alone does
not create one.

## Persistence and authority

Schema 21 adds immutable operation and progress receipts plus fenced current
observations. Keys use source, source reference, request, and stage independently
of canonical matching. Changed membership establishes a new observation basis;
retirement or a smaller denominator is not progress. Loss or revocation reopens
current availability without erasing the retained history.

Use one shared bounded evidence session per refresh batch under the existing
H13 artifact-work gate. Recheck enumeration, evidence revision, restoration
epoch, accepted inputs, and captured policy before committing observations.
Unknown, incomplete, stale, or expired observations cannot establish completion.
Verify the enumeration content address and membership digest before using its
requests. Bound that check to 128 members and each visit to 32 page opportunities;
larger enumerations remain unassessed. Rotate events and pages without skipping
work merely because earlier checks depleted the shared budget.

## Reporting and limits

Show recorded successful operations separately from qualified observed progress.
Sampling can miss transitions, so this first slice explicitly has incomplete
progress coverage. It does not infer eligible time or enable no-progress alarms.
Artifact restoration without a new operation remains visible as regained
availability; a later repair-specific success receipt needs its own proof.
Parent artifact support is explicitly unassessed by this observer. The separate
inventory check still governs current event completion.

Test real commit and rollback paths, shared and aggregate evidence, membership
changes, legacy positives, corruption, revocation, restoration, reacquisition,
resource exhaustion, concurrent invalidation, pauses, and checkpoint preservation.
These additions remain outside the frozen schema-14 preservation release.

## Links

- [Shared verifier and artifact boundaries](0025-bind-local-coverage-to-artifact-evidence.md).
- [Scheduling contract](../../docs/reference/scheduling.md#failure-pause-and-recovery).
