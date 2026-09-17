# D-0123: Reconcile Step Right through the common projector

Status: Proposed  
Recorded: 2026-09-18  
Accepted: —  
Acceptance source: —  
Topic: Step Right canonical projection  
Supersedes: —  
Superseded by: —

## Decision

Normalize Step Right round observations into the common event contest
projection. Reconcile overlapping canonical contest and round keys with other
sources before writing one event scope. Route event-detail metadata through a
companion source-index observation, and increase the projector version to 20.

Keep Step Right anonymous judges scoped to one round. Do not assign a generic
final bib to either partner, merge a name-only final across pages, project
preliminary callback marks or outcomes, attribute marks to the printed roster,
or invent a score-sheet URL.

## Why

Appending a second source-specific projection allowed duplicate canonical keys
to overwrite earlier evidence at the writer boundary. The common reconciler
keeps one canonical key and applies the existing cross-source rules. Event
details also need normal source-index invalidation so their better name and
date replace listing metadata without losing the listing location.

This choice changes local projection behavior only. It does not enforce an
admission policy, accept a historical year, enable collection or publish data.

## Links

- [D-0121](0121-withhold-step-right-preliminary-callback-projection.md)
- [Step Right source reference](../../docs/reference/sources/step-right-solutions.md)
- [Step Right next controls](../investigations/2026/stepright-next-controls-2026-09-17.md)
