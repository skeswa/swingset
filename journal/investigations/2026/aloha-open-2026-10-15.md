# Why The Aloha Open 2026 shows no results

Date: 2026-10-15 (UTC), production read around 13:00 UTC  
Type: Investigation  
Topic: Event identity  
Related plan or decision: [missing 2026 results](missing-2026-results-2026-10-15.md), [event matching](event-matching-2026-10-13.md), D-0253, D-0254

## Summary

The results are already published, on the wrong copy of the event. Release
`28f8196` (`cand_df599959f33440cf`) holds two September 2026 Aloha Open events:

| Event                    | Series                | Holds                                                          |
| ------------------------ | --------------------- | -------------------------------------------------------------- |
| `2026-09-aloha-open`     | `wsdc-355`, `listed`  | 7 contests, 190 entries, 42 registry placements; dated 09-23   |
| `2026-09-the-aloha-open` | `slug-the-aloha-open` | calendar facts only (Wailea, alohaopenwcs.com, 09-23 to 09-27) |

So the scoring.dance listing is collected, parsed and accepted, and it reaches
the release (parser 4, `accepted`, mapped `name_date` to `2026-09-aloha-open`).
What failed is the merge of that event with the calendar's. The Aloha Open is a
WSDC points event: the registry reports 42 placements for `wsdc-355` in
2026-09 (first seen 2026-09-29), after 43 in 2025-10 (a trial event in 2024).

## Where it stops

The order of arrival caused it:

1. 2026-09-09: the calendar lists "The Aloha Open". No registry occurrence
   exists, so the calendar unit makes `2026-09-the-aloha-open` with a `slug-*`
   series.
2. 2026-09-28: the inventory reads the scoring.dance listing "Aloha Open
   2026". The reviewed rows `Aloha Open 2024` and `Aloha Open 2025` in
   `series_aliases.csv` share its slug `aloha-open` (years are dropped), so it
   resolves to `wsdc-355`. No held occurrence exists yet, so `match_listing`
   names no event and the inventory inserts `2026-09-aloha-open`, series
   `wsdc-355`, `held = listed`.
3. 2026-09-29: the registry reports the occurrence. `prepare_inventory`
   (`history.py:73-115`) finds a row of `wsdc-355` in 2026-09 and stops. It
   never marks that row `held`, and never reaches the branch that would adopt
   the calendar row.
4. Since no row is held, the calendar unit (`events.py:92-101`) cannot join one,
   and the map's series rule cannot either. The registry placements go to the
   only `wsdc-355` row, which is the listing's row. The two events never merge.

In 2025 the registry came first, so the calendar row was adopted and the merge
worked (`2025-10-the-aloha-open`).

No override fixes this. An `event_aliases.csv` row sending `scoringdance:375`
to the calendar event would move the contests but leave the points behind,
which splits them again the other way.

## Same shape elsewhere

A census of state found no other calendar `slug-*` event in 2025 or 2026 with
an unmatched results listing in its dates. City of Angels 2027 matches by
name, but its listing's 2027 date is wrong and an override already handles it.
Step 3, the adoption gap, also leaves three other 2026 occurrences unheld:

| Series     | Rows                                                                            | Effect                                                                                   |
| ---------- | ------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------- |
| `wsdc-300` | `2026-09-austin-rocks`                                                          | points on the row; only `held` is wrong                                                  |
| `wsdc-338` | `2026-09-sea-dance-fest`                                                        | the same                                                                                 |
| `wsdc-357` | `2026-09-wcsparty` (12 contests), `2026-09-wcs-party-in-vienna` (88 placements) | a split: WCS Party in Vienna has results, which the missing-2026 page lists as not found |

## Fix

A code change to `project/history.py`, belonging to the matching work. When the
registry reports an occurrence whose rows are all unheld, it adopts the one
matching calendar row as it already does. It then deletes rows that no scope
owns, which are the inventory's own listing rows. With no calendar row, it marks
the single unheld row `held`. Simulated on production rows, it adopts
`2026-09-the-aloha-open` and drops `2026-09-aloha-open`. It marks Austin Rocks
and Sea Dance Fest held, and leaves WCS Party alone because it has two rows.
The map's D-0253 rule then moves the Aloha contests to the calendar event.
Marking the listing's row held is not enough by itself. The calendar unit does
not re-run when events change, so `2026-09-the-aloha-open` would stay as an
empty twin. A scratch test reproduces the defect and passes with the change. So do
`test_project_map.py`, `test_history_*.py`, `test_event_history.py` and
`test_h15_acceptance.py`.

WCS Party needs a reviewed row:
`scoringdance,scoringdance:441,2026-09-wcs-party-in-vienna,"WCSParty 2026 on scoring.dance (Sep 17) is WCS Party in Vienna, wsdc-357; registry 2026-09 has 88 placements on the calendar event"`.

## Evidence and reproduction

Read-only scripts in the session scratch (`scratchpad/p3-aloha/`, not
retained): `q1.py` to `q8.py` read `state.sqlite` with `mode=ro`; `d1.py` and
`d2.py` read the release parquet; `sim.py` applies the proposed rule to
production rows; `aloha-history.patch` and `aloha-test.py` are the change and
its test.
