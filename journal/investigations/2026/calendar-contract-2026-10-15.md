# A WSDC calendar admission contract, measured on production

2026-10-15. Read-only. The working-copy contract (D-0257) and adapter ran over
every stored `wsdc_calendar.events` body on the worker; the release
simulation compares candidate `cand_df599959f33440cf` (`28f8196`) with live
state's `events` rows. Scripts stayed in scratch.

## The contract over 144 stored captures

| Outcome                        | Captures | Why                                                                                            |
| ------------------------------ | -------: | ---------------------------------------------------------------------------------------------- |
| Passes                         |      111 | 43 table and 1 superseded live, 18 card, 26 FullCalendar, 23 map; body counts equal row counts |
| `waiting_for_inputs`           |       29 | `/print-event-list/` captures with no rows: an empty listing is no witness                     |
| Extract failure, no generation |        4 | 2016 map markers print no date (D-0071)                                                        |

Every body witness matched its rows, no page had a pagination link, and no
row carried an unreviewed class, type or flag. Before two adapter fixes,
17 map captures and one card capture failed the date-order guard:

- **Cross-year spans.** The map prints `Dec 28 - Jan 1, 2020` with one year;
  parser 7 dated the start 2020-12-28. The adapter now gives a
  December-to-January span the previous year for its start.
- **Backwards spans.** `Feb 13 - 9, 2020` (map), `Feb 13 2020 - Feb 09 2020`
  (cards) and 24 FullCalendar rows such as `2017-10-27 - 2017-10-02` are the
  source's errors. They become `impossible_listing_date` findings and the
  rest of the capture is admitted. 30 rows in all.

The flag link printed `transparent` on 59 stored rows and nothing on 29. Every
`transparent` row is in Russia (captures from 2025-10 on; 2023 captures print
`RUS`). Six country names (`Slovenia`, `Malaysia`, `Ireland`, `Scotland`,
`Latvia`, `Bulgaria`) stood where a code belongs. Parser 7 passed all of
these through, so release `28f8196` publishes `transparent` as a country for
five events. The adapter now keeps no country for a blank or `transparent`
flag and maps the six names.

## A defect that would have kept every archived capture out

The adapter scopes its rows `calendar/wsdc`; the writer stores an archived
capture's rows as `calendar/wsdc-history`. The build matches stored rows
against the accepted generation's result, so all 67 cited Wayback captures
would have stayed `legacy_unassessed` after activation. With the same rule
applied on both sides, 37 captures match row for row and 31 differ only in
rows the new adapter changes (201 rows), which admission rewrites.

A second one: a live capture replaces every row of its watch. The 2026-08-13
Wayback capture was read on the live watch, so the first live fetch after the
watch reopens would delete its 169 rows, the summer 2026 listings. The writer
now leaves an archived capture's rows alone.

## What activation would change in the release

All 1,008 `events` rows that cite a calendar capture cite one that passes.

| Measure (all years) | `28f8196` | After |
| ------------------- | --------: | ----: |
| Events              |     2,191 | 2,532 |
| Day-precision dates |     1,937 | 2,320 |
| Websites            |     1,335 | 1,728 |
| Cities              |       165 |   160 |
| Countries           |       165 |   153 |

The gain is 42 published events that get dates and 52 that get a website,
plus 341 events no release carried. For 2026: dated events 173 to 201,
websites 143 to 179. The 21 registry-only
events of the missing-results search all gain dates and website; none gains
a place, because the history projection writes no city or country. Ten WDR
result events trade the baseline's month-wide dates for the calendar's days.
City of Angels 2026 loses scoring.dance's 2027 date (item 33). No calendar
date replaces a day-precise date from a results site.

Worse for some events:

- **Twins.** 153 of the 341 new events share series and month with a
  published event: `2020-09-bavarian-open-cancelled-due-to-covid-19` beside
  `2020-09-bavarian-open`. The history projection writes a second row for a
  listing whose name gains a note. Publishing both duplicates them.
- **Place.** Trilogy Swing, WCS Party in Vienna, Austin Rocks, Sea Dance
  Fest and the 2026 Global Grand Prix lose city, region and country. The
  release shows the baseline's row from the live calendar; the accepted
  header comes from the history projection, which has no place (item 29).
- Six `transparent` or blank countries become null, which is right.

Also new: 29 held registry occurrences with registry placements that no
release has carried, such as `2025-06-swing-fiction`.

## What this means

The contract itself admits every capture with dated rows. Activating it
publishes what the projections hold, so the twins and the missing place
are projection work in `project/history.py`; carrying the listing's place
would also give 304 events a city, 41 of them in 2026.
