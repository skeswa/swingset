# D-0035: Prove page retirements on one enumeration edge

Status: Proposed  
Recorded: 2026-09-16  
Accepted: —  
Acceptance source: Coordinator implementation authorization within the owner's continuing plan; separate owner acceptance is not recorded.  
Topic: Event retirement evidence  
Supersedes: —  
Superseded by: —

## Decision

Schema 23 records bounded observations of explicit page withdrawals between a
current enumeration and its immediate predecessor. Reuse one pure declaration
projection for enumeration construction and proof. Verify the exact admitted
replacement, affected prior claims, normalized request difference, and independent
ownership through the existing shared evidence session.

The replacement's accepted decision must follow the predecessor's creating
admission and every affected claim's acceptance. Check serialized decision IDs;
do not equate the event predecessor with the source unit's previous generation.
Predecessor chronology requires verified generation metadata and its exact accepted
decision, without requiring unrelated predecessor artifacts. The replacement and
every affected claim require their actual admitted artifact support.

Use one mutable current-edge observation and immutable withdrawal receipts.
Receipt identity binds the event, enumeration edge, exact generation/decision
support, and retired request identities. It excludes verification time, observer
policy, and resource settings. Rechecking or restoring files cannot create a new
withdrawal.

## Why

An index can omit a page without authority, or another parent or admitted child
result can still support it. A smaller denominator alone cannot prove retirement.
An old accepted generation also cannot authorize a later withdrawal merely because
it belongs to the same unit. Exact ownership, admitted declarations, and admission
order close those gaps.

## Alternatives

- Trusting `removed_json` is insufficient: it is not part of the enumeration's
  content address. Recompute the difference from verified membership.
- Walking the whole predecessor chain would grow verification with history.
  Assess one edge and report skipped history as unknown.
- Treating an empty successor as a retired event would confuse known obligations
  with an unknown page universe. Whole-event retirement remains unknown.

## Consequences

The existing progress visit, shared budget, H13 boundary, and write fence own this
work. An edge delayed by a used-up allowance gets a fresh-budget opportunity.
An intrinsically oversized proof records unknown and yields to ordinary page
verification. Parent-priority visits also process or defer the edge. There is no
new scheduler or request loop.

Reports expose `page_retirement` for the immediate edge. Mutable observations
cannot override fixed unknown whole-event/history fields or immutable proof
counts. Revocation, incompatible policy, stale fences, expiry, and missing proof
artifacts make current evidence unassessed; historical receipts remain. This
neither deletes data nor advances successful operation progress.

## Links

- [Design and test matrix](../investigations/2026/event-page-retirement-proof-2026-09-16.md)
- [Reporting](../../docs/reference/operations.md#event-completion-reporting-h14-extension)
- [Persistence](../../docs/reference/state.md#event-completion-persistence-h14-extension)
- [Implementation plan](../../docs/plans/history-and-recovery.md)
