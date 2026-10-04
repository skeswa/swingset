# Why fetched score sheets were parked, and which can be admitted

Date: 2026-10-06 (UTC)  
Type: Investigation  
Topic: Admission review  
Related plan or decision: [Row-level result holds](../../../docs/plans/row-level-result-holds.md); D-0180, D-0187

## Summary

On 2026-10-06, 638 fetched pages (632 from scoring.dance, the oldest from
2026-09-09) were parked as `admission_needs_review`, so new score sheets
reached no candidate. A review agent read every blocked generation and sample
stored bodies, read-only and with no live fetch. Four kinds of unrecognized
round-sheet column caused all 1,743 blocked round generations. A contract
version 5 admits 311 of the 336 parked rounds (about 13,900 rows across about
123 events) and keeps 25 blocked. The owner kept the 23 rounds whose remark
contradicts the displayed outcome blocked.

## Findings

| Group                                                                             | Parked units | Decision                                                   | Admitted          | Blocked           |
| --------------------------------------------------------------------------------- | ------------ | ---------------------------------------------------------- | ----------------- | ----------------- |
| Round, empty header in column 2: the partner's name on single-table couple rounds | 124          | handle: label it `Follower`                                | 124               | 2 two-table pages |
| Round, empty column after Σ: chief-judge adjustment (`+1`, `+0.89`)               | 134          | exclude: changes only the excluded displayed sum           | with the next row |                   |
| Round, last info column: `CJA`, tie-breaks, `VR PASS`, `V1`/`VP`, `RV` notes      | 201          | exclude known values; new guard blocks an outcome conflict | 178               | 23                |
| Round, random 2-3 letter judge codes (Champions finals)                           | 8            | exclude                                                    | 8                 |                   |
| Event page, "results aren't published yet", no date                               | 70           | keep blocked                                               |                   | 70                |
| Event 179 (Hippmann Fun-Competition 2024), lists no competitors                   | 1            | keep blocked                                               |                   | 1                 |
| WDR rounds, row "loss" from dict key order in the parser                          | 2            | handle: parser fix; `SVW` is a warning                     | 2                 |                   |
| eepro strictly rounds, empty trailing cell                                        | 2            | after the mm2025 duplicate cleanup                         |                   | 2                 |
| WSDC registry dancers with blank records                                          | 2            | keep blocked                                               |                   | 2                 |

Outcome conflicts kept blocked (23 rounds), for example: "DQ Lead" beside 1st
and 3rd (Festival City Swing Edinburgh 2026, Newcomer Jack & Jill final),
"DISS" on a called-back row (Winter Coast Swing 2025), "disqualified" on a
called-back row (SoSwing Convention 2026), "Video review fail" at 1st (Midland
Swing Open 2026, Rising Star final), `SW` on a called-back row (NeverlandSwing
2023). The same remarks on rows already eliminated are excluded (7 rounds).

Activating contract 5 re-parses all 5,853 stored round snapshots from stored
bodies; the 5,237 accepted under version 4 should be byte-identical, which the
rehearsal must confirm.

## Conclusion and follow-up

Contract 5 is being implemented, rehearsed and checked before deployment.
The 23 conflicts wait for [row-level result holds](../../../docs/plans/row-level-result-holds.md).
The mm2025 duplicate pages and four smaller defects (dropped `SVW` marks,
callback sums without chief-judge adjustments, Pro-Am follower labels, the
key-order bug elsewhere) are maintenance items 18 and 19.

## Evidence and reproduction

The full adjudication, with sample snapshot ids and body quotes, and its
read-only query scripts stayed in the session scratch directory and are not
retained. The counts come from `source_generations.report_json` and
`work_queue` on the production database at about 2026-10-06 22:00 UTC.
