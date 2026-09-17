# D-0122: Version Step Right admission without removal authority

Status: Proposed  
Recorded: 2026-09-18  
Accepted: —  
Acceptance source: —  
Topic: Step Right source admission  
Supersedes: —  
Superseded by: —

## Decision

Give `steprightsolutions.index`, `.event`, and `.round` separate version-1
admission contracts. Require exact retained body-shape and coverage witnesses,
and grant no removal authority for any of the three kinds. Keep policy
enforcement separate until an exact corpus has an independent review.

## Why

Five retained bodies establish one index, a metadata-only event, a
results-bearing event, a preliminary and a final. The contracts can account for
those shapes and fail closed on changed enumeration, layout, callback or
placement evidence. An archived page does not prove that omitted historical
rows disappeared, so it cannot retire earlier evidence.

This choice adds no source watch, historical-year acceptance or publication.

## Links

- [Step Right source reference](../../docs/reference/sources/step-right-solutions.md)
- [Step Right next controls](../investigations/2026/stepright-next-controls-2026-09-17.md)
- [Admission reference](../../docs/reference/recovery/admission.md)
