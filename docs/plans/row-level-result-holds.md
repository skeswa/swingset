# Row-level result holds

Admit a score sheet even when one row contradicts itself, and hold back only
that row's result until someone decides it.

## Why

A scoring.dance round sheet sometimes shows a dancer as called back or placed
while a note on the same row says the dancer was disqualified or failed a
review: "DQ Lead" beside a 1st place (Festival City Swing Edinburgh 2026,
Newcomer Jack & Jill final), "DISS" on a called-back row, or "Video review fail"
on a 1st place (Midland Swing Open 2026, Rising Star final). Contract version 5
blocks the whole page for this (guard `source_remark_outcome_conflict`), so one
contested row keeps every other competitor in the round out of the dataset.
On 2026-10-06 that was 23 rounds
([adjudication](../../journal/investigations/2026/admission-adjudication-2026-10-06.md));
the owner kept them blocked until this plan lands.

## The change

1. **Admission holds the row, not the page.** The guard stops failing the
   generation. Its report lists each held row (snapshot, row, bib, displayed
   outcome, remark text). The page is accepted; held rows count as interpreted
   and withheld, so the row-count guard still balances.
2. **Each result gets a status.** A new enum `result_status` on the
   observation and on published `entries` and `placements`:

   | Status          | Meaning                    | Published                                         |
   | --------------- | -------------------------- | ------------------------------------------------- |
   | `shown`         | today's behaviour          | callback or placement as displayed                |
   | `held_conflict` | the remark contradicts it  | entry and judges' marks; no callback or placement |
   | `disqualified`  | decided: removed           | entry marked disqualified; no placement or points |
   | `upheld`        | decided: the result stands | as displayed                                      |

   Judges' marks are always kept. Other competitors keep their displayed
   placements.

3. **Decisions live in an override file.** `overrides/result_adjudications.csv`
   (`event_id, round_url, bib, decision, evidence, reviewed_by, reviewed_at`),
   following `finding_reviews.csv`. Review agents fill it (D-0180).
4. **The registry supplies evidence where it can.** In points divisions, WSDC
   points for that dancer at that event mean the result stood; points for the
   round but not for that dancer suggest a disqualification. This needs a
   published identity link for the dancer, and the current build has 0
   confirmed links, so until the
   [identity recovery](data-quality.md) ships, agents decide every row.
5. **The dataset says why.** Each held row adds a `review_queue` item, and the
   `result_status` column tells users why a result is missing.

## Work

| Step | What                                                                                       | Checked by                                                          |
| ---- | ------------------------------------------------------------------------------------------ | ------------------------------------------------------------------- |
| 1    | Decision record (data model), schema migration, enum, data-model and schema-history docs   | core suite, migration test on a copy                                |
| 2    | Contract version 6 for `scoringdance.round`: held rows instead of a failing guard          | fixtures for the 23 rounds; the parity script shows no other change |
| 3    | Projector: drop held results, apply `result_adjudications.csv`, emit `review_queue` items  | projection tests per status                                         |
| 4    | Registry evidence as a proposed decision, never applied without a reviewed row             | tests with matching and missing registry points                     |
| 5    | Publication schema and the gate: `result_status` columns; held rows never reduce a measure | build on a copy; gate verdict                                       |
| 6    | Rehearse on a copy, independent check, deploy with receipts                                | the 23 rounds admitted, held rows withheld, all else unchanged      |

## Done when

- The 23 rounds are accepted, with their held rows withheld and listed in
  `review_queue`.
- No other accepted generation changes, measured by the parity script.
- An adjudication row changes one result on the next build and nothing else.
- A published candidate passes the gate with the new columns.

## Schedule

Release work, about one week of agent time. It builds on round contract
version 5 and on phase 5's code, so it starts after contract 5 is deployed
and lands after the phase 5 switch (not before 2026-10-11). Narrowing the
contract scopes (maintenance item 20) comes first, so its contract 6 re-parses
only round sheets. Order: segmented backups, contract 5, phase 5, item 20,
then this plan.

## Open questions

- Whether `SW` is an outcome at all (one round, meaning unknown).
- Whether a held couple row holds both partners or only the named role
  ("DQ Lead").
