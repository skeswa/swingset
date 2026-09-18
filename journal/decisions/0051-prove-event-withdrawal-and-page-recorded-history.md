# D-0051: Prove event withdrawal and page recorded history

Recorded: 2026-09-17  
Decided by: agent  
Topic: Source-event progress and retirement  
Supersedes: —  
Superseded by: —

## Decision

Keep whole source-event withdrawal separate from withdrawal of its known pages.
Require an admitted authoritative replacement that omits a previously declared
event, a verified immediate page-withdrawal edge, and a complete bounded check
of all retained accepted declarations for that source. Empty event declarations,
independent claims, missing artifacts and an incomplete evidence domain prevent
a positive withdrawal claim.

Store immutable withdrawal receipts separately from current verification proofs.
Rechecking a changed evidence domain can replace the current proof without
recording another withdrawal. Neither action records successful acquisition,
interpretation or publication.

Expose complete pages of recorded accounting, enumeration, page-retirement and
source-event-retirement history. Pin a high-water mark and use bounded keyset
pagination. State when recording started; do not infer lifetime completeness,
unobserved transitions or unknown legacy history.

## Why

Removing all listed pages can leave an explicit event declaration or an
independent parent's claim. A sampled fleet view cannot disprove those claims.
The new proof examines at most 128 retained source generations under the shared
verification budget and stays unknown if it cannot inspect the whole domain.
Retained recipe ownership also prevents mutable watch metadata from hiding a
declaration. Source revisions, admission high-water marks, current policy and
event fences invalidate stale current proofs.

Immutable historical receipts remain inspectable after invalidation or restore.
One receipt per withdrawal enumeration avoids counting repeated verification as
multiple retirements. Separate history pages avoid an unbounded doctor refresh.

## Consequences

Schema 27 adds immutable source-event withdrawal receipts and verification
proofs. Large sources and events with no proven page-withdrawal edge can remain
unknown. This is an implemented local reporting choice, not owner acceptance or
a declaration of deployment, publication or complete fleet history.

## Links

- [Reporting contract](../../docs/reference/data-model.md#recorded-source-event-history-and-retirement)
- [Implementation evidence](../investigations/2026/event-retirement-history-2026-09-17.md)
- [Extension acceptance audit](../investigations/2026/event-extension-acceptance-2026-09-17.md)
