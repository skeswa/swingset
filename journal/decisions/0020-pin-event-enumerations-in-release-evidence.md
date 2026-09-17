# D-0020: Pin event enumerations in release evidence

Status: Proposed  
Recorded: 2026-09-16  
Accepted: —  
Acceptance source: The owner authorized continued history and recovery implementation. This working-source extension has not received release acceptance.  
Topic: Release coverage  
Supersedes: —  
Superseded by: —

## Decision

Select source-event enumeration witnesses inside the release read snapshot and
cutoff. Bind exact membership and supporting evidence into the existing build
dependency manifest, semantic fingerprint, reuse decision, and closure proof.
Render source-event coverage only from that pinned witness. Scheduling pressure
observations are not release evidence.

Preserve unresolved source events with nullable canonical mappings. Count
distinct requests, with explicit unknown pagination and legacy denominators.
Read canonical mappings from the release selection. Compute represented pages
from the final emitted result facts after historical suppression; discovery
support alone does not represent a result page.

## Why

Adding current inventory counts during rendering would let an unchanged build
trigger reuse old membership, miss revoked support, or import parent changes
after the release cutoff. The witness must participate in existing validation
and any cached proof's read set. Harmless later additions must leave an otherwise
valid pinned release publishable; its successor can select the new enumeration.

## Consequences

Evidence checking adds work and must use bounded memory. Disclose verification
limits as unknown coverage, never zero or completion. Existing coverage metrics
remain intact; overlapping transport rows are not disjoint populations.
Published event progress comes only from an acknowledged baseline and verified
publication receipt. A local candidate can report awaiting publication but
cannot advance published counts.

This extension changes only the working source. It does not alter or expand
the frozen original H16 candidate currently undergoing scratch validation.

## Links

- [Coverage fields](../../docs/reference/data-model.md).
- [History and recovery plan](../../docs/plans/history-and-recovery.md).
- [Closure proof reuse](0007-bounded-closure-proof-reuse.md).
