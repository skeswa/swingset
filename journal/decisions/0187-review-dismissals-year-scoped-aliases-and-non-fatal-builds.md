# D-0187: Reviewed dismissals, year-scoped aliases, and non-fatal build failures

Recorded: 2026-09-21  
Decided by: agent, under D-0180 and D-0183  
Topic: Historical review  
Supersedes: —  
Superseded by: —

## Decision

Three changes so review agents can carry a year to acceptance:

- `overrides/finding_reviews.csv` records a reviewer's dismissal of one
  finding by kind and subject. A dismissed subject is not opened again by the
  inventory projector, and a dismissed parse warning no longer counts as a
  phase-one gap for its year. The row names the reviewer and the reason;
  the decision is only ever `dismiss`.
- `overrides/series_aliases.csv` rows may carry `from_month` and `to_month`
  (inclusive `yyyy-mm`). A dated row applies to listings whose reporting
  month falls inside it and beats an undated row for the same name, so a
  printed name can point at the registry id that held it in each era.
- A build that cannot assemble a consistent candidate is reported in the
  cycle summary and logged, and the cycle continues into acquisition and the
  offline phase.

## Why

After the aliases landed, every year still held findings that no override
could close: ten printed names whose registry id changed between years, a
few listings the calendar and a platform date a day apart, and phase-one
warnings such as a typo'd year in a 2010 source row. Each needs a judgment
that the pipeline cannot make and that D-0180 assigns to a review agent; the
judgment has to live in an input so it survives re-projection.

The build failure after the candidate 010 drain came from pairing a fresh
projection with a link generation from five days earlier, and because the
failure aborted the cycle, the link units that would have refreshed it never
ran. Seventeen cycles in a row failed the same way.

## Links

- [Backfill reference, data model](../../docs/reference/backfill.md#data-model-changes)
- [D-0180](0180-agents-adjudicate-year-acceptance-and-identity-review.md)
