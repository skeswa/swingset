# Independent review of WSDC calendar contract 1 before activation

Date: 2026-10-19 UTC  
Type: Outcome  
Topic: Source admission  
Related plan or decision: [D-0257](../../decisions/0257-admit-wsdc-calendar-captures-under-contract-1.md), [D-0260](../../decisions/0260-an-unreported-edition-is-one-row-per-series-and-month.md)

## Summary

Verdict: **approve with conditions.** Contract 1 reads every calendar capture
on the worker correctly. A second reader, written for this review and sharing
no code with the adapter, re-read all 143 decodable bodies. It matched every
dated row on name, dates, website, type, place text, country and row classes.
It also matched every withheld listing. The refusals are right. No accepted
generation states a fact its body does not print.

One new wrong value would still publish after activation. It comes from the
history projection, not the contract. A 2016 to 2020 card listing prints a
street address in its place text, and `_calendar_place` turns that address
into a `region`. Five events would publish one, such as
`2021-01-freedom-swing-dance-challenge` with the region
`4727 Concord Pike, DoubleTree by Hilton Hotel, Wilmington, DE 19803 NJ`.
Today the release shows these five with no place.

Reviewer: Claude (independent reviewer agent), read-only. Production tree c049
(`main` 914581a), policy `shadow`, code contract `1`.

## What was checked

`swingset contract status` (read-only) found 147 contract-1 generations: 114
staged, 29 `waiting_for_inputs`, and 4 `needs_review`. Each one was dumped from
read-only SQLite together with its stored body and extract.

| Check                                                                    | Result                                                                                                                                                          |
| ------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Printed listings = dated rows + `impossible_listing_date`, per capture   | Holds in all 143 decodable captures (independent count = report `source_count` = interpreted)                                                                   |
| Independent re-read vs generation rows (about 19,000 rows)               | Identical. The only differences were the review script's own JS unescaping                                                                                      |
| FullCalendar end is exclusive (adapter subtracts a day)                  | Confirmed: 1,388 of 1,389 rows match the same listing in cards, map or table. The odd one is a source edit (Sacramento All Swing 2017)                          |
| One-year `Dec 28 - Jan 1, 2020` spans take the year before for the start | Confirmed: all 60 map rows match another layout's start                                                                                                         |
| `transparent` and blank flags                                            | All 71 `transparent` rows are in Russia; those and 35 blank rows keep no country. Six printed names map to the right code                                       |
| Unknown flag finding                                                     | No production capture prints one; only the unit test covers it                                                                                                  |
| 29 `/print-event-list/` captures wait                                    | Each is the empty search form with no rows: correct, it is not a witness                                                                                        |
| 4 undated 2016 maps fail                                                 | Markers print `'Orange County s 37th annual'` with no date: correct refusal                                                                                     |
| Removal authority                                                        | Live watch holds the 2026-08-13 Wayback rows (169, `wsdc-history`) plus only the newest live capture (157, `wsdc`) after the 10-16, 10-17 and 10-19 live parses |
| Hand spot-check                                                          | 30 rows read against raw markup, across all four layouts and every special case. All right                                                                      |

## Findings that do not block

- Source placeholders. In 2016-11 to 2017-06 captures, ten listings print one
  start date for an edition whose dates were not set, for example
  `Midwest Westie Fest Aug 2, 2017`. Later captures give the real span and win
  as the newest listing. Five keep a one-day date because no later listing
  exists, and Arizona Dance Classic 2017 is published twice (`2017-07` and
  `2017-08`). These are already in release `265e1ca`; activation changes
  nothing here.
- `US OPEN SWING DANCE CHAMPIONSHIPS, INC \"THE OPEN\"` (2021) keeps a
  backslash. The source escaped it twice, so the row is faithful but ugly.
- Twenty-seven `(Unconfirmed)` listings exist as their own event rows, such as
  `2023-08-riga-summer-swing-unconfirmed`. They are withheld today. The
  status model has no `unconfirmed` value.

## Conditions

1. Before or in the activation deploy, stop `_calendar_place` from taking a
   region from a card-layout listing, whose place text holds the street and
   venue, or accept and list the five rows as a known limit. The five are
   Korea Westival 2017, Swingvester 2021, Charlotte Westie Fest 2021,
   New Year's Swing Fling 2021 and Freedom Swing Dance Challenge 2021.
2. Turn off `KEEP_CANCELLED_TWINS` in the same deploy, as D-0260 requires;
   `contract activate` refuses until then.
3. The post-check compares the release with `265e1ca`. It confirms that no
   new same-series, same-month duplicate and no `-unconfirmed` row publishes
   unless that listing is the edition's only one.

## Cohort

17 generations, in the session scratch file
`/private/tmp/claude-501/-Users-skeswa-repos-skeswa-swingset/d2437130-eb3d-42eb-9784-f088906a5e0a/scratchpad/calreview/cohort.json`. It is a JSON list of objects, each with a
`generation_id`. Production digest by `policy.corpus_digest`:
`07fcd84a353d392527be9b9d650d42f442d98bb44ab244d9655123eaeb1da221`. All 17 are
`wsdc_calendar.events` contract 1, none is packed, and 15 pass. The cohort
covers both newest live captures (2026-10-16, 2026-10-19), the 2026-08-13
Wayback capture on the live watch, five Wayback tables (country names, cancel
and unconfirmed classes, `tr_events_load`), two card captures (one with a
backwards span), two FullCalendar captures (three backwards spans), three maps
(pin `1`, pin `white`, Dec-Jan span, `Feb 13 - 9, 2020`), one empty print page
and one undated 2016 map.

## Evidence and reproduction

The scripts stayed in scratch (`dump.py`, `indep.py`, `spot.py`, `ev*.py`,
`validate.py`), as did every read. They used read-only SQLite and made no
source requests.
