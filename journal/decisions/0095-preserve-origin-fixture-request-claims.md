# D-0095: Preserve origin fixture request claims

Recorded: 2026-09-17  
Decided by: agent  
Topic: Bounded origin fixture accounting  
Supersedes: —  
Superseded by: —

## Decision

Keep exact DCN fixture acquisition outside ordinary source admission. Bind the
reviewed packet, source 003, schema 28, held system and six inactive ordinary
units before execution and recheck live interlocks before each request. An
explicitly disabled ordinary DCN source remains a kill switch.

Use shared H13 admission, host budgets and completion-based spacing. Persist a
one-event-per-UTC-day claim and the operation's request sequence before the
admission transaction commits. A crash may consume unused capacity; it must not
permit a duplicate request or another event on the claimed day. Use the actual
grant day after any spacing wait. Preserve claims in the top-level
`dcn-origin-event-days.json` checkpoint member.

Retain exact robots URL, redirect chain, body hash and fetched time in
`dcn-origin-robots-cache.json`. A fresh shared cache without this proof cannot
be silently refreshed. Preserve both sidecars through checkpoint and restore.
Keep captured bodies in a new quarantine with no watches or interpretations.

## Why

The held schema-28 runtime has shared accounting but no durable fixture-specific
event-day claim. A sidecar closes that gap without migrating production for an
acquisition tool. Claim-before-commit ordering closes the crash gap between a
paid request and its daily-event limit. Exact cache provenance prevents a body
from a different robots URL from granting access. No-cookie transport, bounded
gzip decoding and separate admission/reservation/dispatch receipts make the
four-request limit auditable.

This engineering choice implements the already authorized bounded acquisition
under D-0087. It does not record owner acceptance of D-0090 or this choice, admit
new ordinary source kinds, accept a year or authorize a wider crawl.

## Links

- [Standing authority](0087-authorize-remaining-v2-acquisition-and-operations.md)
- [Exact origin scope](0088-scope-origin-score-pdf-controls-after-empty-archive-lookups.md)
- [Fixture source policy](0090-bind-an-explicit-quarantine-source-policy.md)
- [Runner implementation and review](../investigations/2026/dcn-origin-runner-2026-09-17.md)
