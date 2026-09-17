# D-0037: Account for supported unavailable page gaps

Status: Proposed  
Recorded: 2026-09-16  
Accepted: —  
Acceptance source: Implementation authorized by the coordinator within the owner's continuing plan; separate owner acceptance is not recorded.  
Topic: Event accounting  
Supersedes: D-0034's requirement that every accounted page be interpreted  
Superseded by: —

## Decision

An observed interpreted page or a verified unavailable-origin response accounts
for one current, known page obligation. Every required parent must still have
usable support. Keep acquired, interpreted, and unavailable counts separate.
Neither a gap observation nor restored evidence creates successful progress,
publication, permanent absence, or whole-event completeness.

Reuse [D-0036's verifier](0036-count-pinned-unavailable-origin-observations.md)
inside the existing progress session, bounds, controls and observation fence.
Schema 24 retains separate gap observations with exact response support, a
metadata digest and expiry. The shared three-state rule is interpreted OR
unavailable: one true accounts for the page; two false leave it unfinished;
otherwise its accounting remains unknown. Parent priority uses the same rule.

## Why

An explicit unavailable response can explain a listed-page gap without pretending
the results were fetched or interpreted. Counting negative stage observations as
unfinished despite that support would contradict the accounting contract.

Absence checks search all matching source snapshots, including undeclared watch
aliases. A separate source-wide gap revision invalidates those observations on
snapshot changes or watch source moves/deletion. It does not replace the progress
fence, so actual later acquisitions can still qualify against missing baselines.
Rare snapshot support rewrites and watch source changes also invalidate existing positive observations
through the global epoch; ordinary inserts and parse-status updates do not.

## Limits and alternatives

Do not add unavailable as a successful stage or relax the stage-operation schema.
Do not infer unavailability from a missing file, archive error, or unsupported
page. Invalid or incomplete negative evidence remains unknown. Validate every
latest tied terminal response's metadata before selecting support; an invalid
HTTP status cannot be hidden by another tied response.

The catalog remains metadata-only and describes a sampled window. A fresh
drill-down checks actual artifacts; a catalog refresh cannot detect unrecorded
file loss. Source-wide invalidation is deliberately conservative and may require
rechecking unrelated pages from the same source. Whole-event retirement,
pagination completeness, unsupported outcomes, eligible age, and alarms remain
outside this increment.

## Links

- [Investigation and validation](../investigations/2026/event-unavailable-accounting-2026-09-16.md)
- [Accounting rules](../../docs/reference/operations.md#event-completion-reporting-h14-extension)
- [State tables](../../docs/reference/state.md)
