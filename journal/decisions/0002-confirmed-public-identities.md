# D-0002: Publish person IDs only for confirmed matches

Status: Historical; acceptance not yet verified  
Recorded: 2026-09-15; imported from existing design and release evidence  
Accepted: —; original acceptance date and approver not established by this review  
Acceptance source: —  
Topic: Identity  
Supersedes: Legacy design-review item 8 in behavior; original acceptance record unverified  
Superseded by: —

## Decision

Fill the default person ID fields in published entries and judges only for
confirmed matches. Keep probable and other candidate matches available in
`link_candidates`. This record describes an existing policy; it does not
approve new identity matches.

## Why

A plausible name match can attach a result to the wrong person. Consumers
joining default tables may treat that guess as a fact. The correction work
identified unsupported matches that needed withdrawal.

## Alternatives

- **Include probable matches in the default fields.** This was the earlier
  rule. It provides more matches but makes uncertain identities look settled.
- **Discard all uncertain matches.** This loses useful evidence for review and
  for consumers who want to choose their own matching threshold.

## Consequences

Some person IDs stay empty even when a likely candidate exists. Consumers can
inspect candidates separately. Rebuilding and publication must preserve reviewed
rejections instead of silently restoring a withdrawn match.

## Links

- [Current identity rule](../../docs/reference/identity-linking.md#link-status)
- [Legacy review, item 8](legacy-design-review.md)
- [Retained withdrawals](../investigations/2026/v3-identity-withdrawals-2026-09-13.md)
- [Correction release evidence](../investigations/2026/v3-correction-2026-09-13.md)
- [Publication enforcement](../../src/swingset/build/identity_policy.py)
