# D-0240: Round contract 5 admits reviewed columns and blocks remarks that contradict the shown outcome

Recorded: 2026-10-07  
Decided by: owner, 2026-10-06 and 2026-10-07; source: approval of the 2026-10-06 admission adjudication (keep the conflict rounds blocked, no data-model change)  
Topic: Sources  
Supersedes: contract 4 for `scoringdance.round` and `wdr.rounds`  
Superseded by: —

## Decision

scoring.dance round sheets move to contract 5 with extract 5. A one-table
couple round labels its blank column 2 `Follower`. The blank column after
`Σ` (chief-judge adjustment), the last remark column's known values, and
anonymized judge codes in Champions finals are excluded with reasons. A
remark with DQ, DISS, disq, scratch, injury, withdrawal, fail or `SW` on a
row the page shows as called back, alternate or placed fails the new guard
`source_remark_outcome_conflict`, and the whole round stays blocked. The
same remark on an eliminated row is excluded. Unseen remarks stay unknown.

WDR rounds move to contract 5 with parser 4: a Placement Order mark with
`|SVW` passes, and `attribute_group` text comes from a key-sorted mapping.

## Why

The 2026-10-06 adjudication found 336 parked round units held by four
column classes, and two WDR units held by a key-order bug. A rehearsal on
production's stored bodies admits 311 rounds and both WDR units; every one
of the 5,249 accepted generations re-parses byte-identical apart from the
contract label. Publishing a displayed placement or callback next to a
disqualification note could give points to a disqualified dancer.

## Alternatives and consequences

Row-level withholding would admit the 23 conflict rounds and hold back only
the flagged rows, but it changes the data model; the owner kept them
blocked ([plan](../../docs/plans/row-level-result-holds.md)). Until then a
single flagged row withholds its whole round. Published `score_sum` leaves
out the chief-judge adjustment, and the projector still drops a final mark
written `4|SVW` (maintenance item 19).

## Links

- [Parsing reference, source admission](../../docs/reference/parsing.md#source-admission-h6-and-h7)
- [Adjudication](../investigations/2026/admission-adjudication-2026-10-06.md)
