# D-0225: Publish a release only when no measure decreases without approval

Recorded: 2026-09-29  
Decided by: agent, 2026-09-29, under D-0175; source: phase 3 of the [architecture plan](../../docs/plans/architecture.md) (WP9)  
Topic: Publication  
Supersedes: —  
Superseded by: —

## Decision

Every candidate is compared with the published baseline on 16 measures: the
row counts of `events`, `contests`, `rounds`, `entries`, `judges`,
`callback_marks`, `callbacks`, `final_marks`, `placements`, `dancers`,
`registry_placements` and `heats`; confirmed identity links with a WSDC id;
entries and judges with a WSDC id; and distinct events with placements. A
decrease needs a row in `overrides/release_decreases.csv` with the same
measure and the exact baseline value, plus a reason, an approver and a date.
Its `candidate_value` is a floor: any candidate value from it up to the
baseline passes, so a build a day later with a few more rows is not refused
again; a larger decrease is. Otherwise publication is refused with
`decrease:<measure> <from>-><to>`. Nothing is submitted, the candidate stays,
and the build is not a failure. With no baseline the gate passes. A malformed
override file approves nothing and refuses every release with
`overrides_invalid:<line>: <error>`; the loops still capture the bundle, since
only the build reads the file (amended 2026-09-30, phase 3 review: exact
values made the first approval chase a moving count, and a typo stopped
every loop).

The verdict is written into the candidate as `_meta/verdict.json`, hashed in
its manifest and published with the data, but left out of the content hash.
It is also stored on the `release_manifests` row and in the run summary, dry
runs included. A correction-only candidate records its decreases but is
exempt: the suppression or identity rows that asked for it are the approval,
and a takedown must not wait for a second one.

## Why

`138352e` published with zero identity links because nothing compared a
candidate with what was already public. "Strictly better" was a promise, not
a check.

## Links

- [Publishing reference, publication gate](../../docs/reference/publishing.md#publication-gate)
