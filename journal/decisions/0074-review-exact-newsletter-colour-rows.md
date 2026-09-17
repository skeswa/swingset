# D-0074: Review exact newsletter colour rows

Status: Proposed  
Recorded: 2026-09-17  
Accepted: —  
Acceptance source: —  
Topic: Historical source interpretation  
Supersedes: —  
Superseded by: —

## Decision

Use reviewed PDF colour evidence for five exact named and dated sidebar rows
in Volumes 6, 9, 13 and 25. Require both the complete body hash and complete
extracted-pages hash, plus the exact page, name and date pair. Keep source
labels: three `Member Activity` rows and two `Trial Event` rows. Preserve a
member-activity flag without extending the public status enum or interpreting
member activity as trial status.

Keep the registry fallback and colour warning for unreviewed rows. Unknown or
changed bytes and changed extracts cannot inherit a reviewed disposition. This
is implemented locally in parser 8; it is not runtime admission, owner year
acceptance, deployment or publication.

## Why

The retained PDF text-show operators specify nonstroking purple or gray for
these titles and dates, and their printed legends explain those colours.
Volume 13 uses different gray values for the legend and row, so matching one
numeric legend colour would miss a real trial. A general PDF interpreter needs
broader controls. Exact-body dispositions correct the proven rows now without
extending that claim to unsupported graphics states, layouts or other bodies.

## Links

- [Evidence and validation](../investigations/2026/newsletter-colour-review-2026-09-17.md).
- [Events-first source contract](../../docs/reference/backfill.md#events-first).
