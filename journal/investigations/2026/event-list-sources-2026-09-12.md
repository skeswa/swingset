# Sources for a complete list of WSDC events, 2010 to today

> Historical record. Dates, status claims, and instructions below describe the
> original work. See [current status](../../../docs/status.md) before acting.
> [Project journal](../../README.md)

Status: research, 2026-09-12. Evidence: `verification/2026-09-12/event-list/`.
Facts below were checked on 2026-09-12 unless marked **unverified**.

## The question

`swingset` needs one row per WSDC event edition from 2010-01-01 on, with
the right dates, place, and series identity, and nothing invented. The
current design ([backfill](../../../docs/reference/backfill.md#event-enumeration-for-history))
enumerates history from three inputs: Wayback captures of
`worldsdc.com/events/` (none before 2016-10, none in 2017, 2018, or 2022),
results-platform indexes, and registry occurrences at month precision.
This note asks what else exists, per year, and how the pieces fit.

## Short answer

1. **The registry is the only source that says an edition was held.** Every
   pointed edition since 1991 appears there as a series id plus a month.
   It is the floor for every year and needs no new collection.
2. **Day-precision listings exist for every year since 2009.** The WSDC's
   previous site, `swingdancecouncil.com`, published a "Member Registry
   Events" page that the Wayback Machine captured 31 times between
   2009-02 and 2016-02. Each capture lists the next 11 to 16 months of
   registry events with dates, city, and contact. It was not checked
   before; the design marked the pre-2016 domain **unverified**.
3. **The 2017, 2018, and 2022 holes close with two more sources.** The
   `/event-list/`, `/print-event-list/`, `/event-calendar/`, and
   `/events-map/` paths on `worldsdc.com` have captures in 2022 that
   `/events/` lacks, and the quarterly WSDC newsletter (28 issues,
   2016-12 to 2024-05) carries an "Upcoming Registry Events" list with
   dates every quarter.
4. **The registry month is the end month 88% of the time and the month
   after otherwise.** Matching must accept both. Nothing was one month
   early.
5. **No shortcut exists.** `worldsdc.com` exposes no events endpoint over
   its REST API, `points.worldsdc.com` lists no events, and the
   print-event-list page is empty today.

## What "accurate" means here

A row is right when four things hold. Each has a different best source.

| Property                               | Best source                                                         | Fallback                                 |
| -------------------------------------- | ------------------------------------------------------------------- | ---------------------------------------- |
| The edition was actually held          | registry occurrence (points were awarded)                           | a parsed score sheet                     |
| Start and end dates                    | a listing published before or during the event                      | registry month, `date_precision = month` |
| Series identity across renames         | registry series id; an alias table for printed names                | name normalization                       |
| Registry versus trial versus cancelled | calendar row type and flags; newsletter "new registry events" boxes | absence of a registry occurrence (weak)  |

A calendar listing alone does not prove an event happened (2020 and 2021
are full of cancelled listings). A registry occurrence alone does not give
a day. Both are needed.

## Sources by era

| Years        | Held (registry)              | Day-precision listing                                                                                                                      | Notes                                                                                        |
| ------------ | ---------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------- |
| 2010 to 2016 | 80 to 135 occurrences a year | `swingdancecouncil.com` "Member Registry Events" captures: 2009 (2), 2010 (2), 2011 (7), 2012 (5), 2013 (1), 2014 (1), 2015 (11), 2016 (2) | One thin patch: events from 2011-03 to 2011-06 fall between the 2010-03 and 2011-07 captures |
| 2016-07 on   | registry                     | `worldsdc.com/events/` captures (2016: 4 months)                                                                                           | Row format differs from today's: `10th November, 2016 To 13th November, 2016`                |
| 2017 to 2018 | 148, 146                     | newsletters Vol 2 to Vol 9 (quarterly lists); the 2016-11 calendar capture reaches into 2017                                               | No archive capture with data in either year                                                  |
| 2019 to 2021 | 153, 31, 26                  | `/events/` captures; newsletters                                                                                                           | Cancellation flags matter here                                                               |
| 2022         | 92                           | `/event-list/` (14 months of captures 2021-04 to 2022-12), `/print-event-list/`, `/event-calendar/`, `/events-map/`                        | `/events/` has no 2022 capture                                                               |
| 2023 on      | registry                     | `/events/` captures; newsletters to 2024-05; our own daily fetch from 2026-09                                                              |                                                                                              |

Results platforms (Step Right, EEPro, DCN, scoring.dance, WDR) add dates
for events that published sheets and are unchanged from
[backfill](../../../docs/reference/backfill.md#what-exists-for-each-era-verified-2026-09-11-and-2026-09-12).

## Source detail

### `swingdancecouncil.com/ActiveServerPages/UpcomingEvents.asp` (2002 to 2016)

- The WSDC's site until the 2016 move to `worldsdc.com`. The January 2013
  newsletter announced "a new website" for 2013; the ASP page kept being
  captured until 2016-02 and returned 404 from 2016-03 and 301 from 2017.
- Page title "Member Registry Events Page". Controls: "All Events" or
  "Upcoming Events" and "Events In The Next 1..18 Months". Captures hold
  the default view only; the "All Events" view is a form submission and
  is not in the archive.
- Columns: event date (`Aug. 23 - 26*, 2012`, `Nov. 29-Dec. 2, 2012`,
  `July TBD, 2013`), name, location (`Framingham, MA`, `Warsaw, Poland`),
  contact person, phone, ticket contact. No event type, no website
  column (some names link to sites), no year-round id. The `*` after some
  dates has no legend on the page; its meaning is **unverified**.
- 200-status captures and the window each one lists (first and last row):

  | Capture                                | Rows | Lists              |
  | -------------------------------------- | ---- | ------------------ |
  | 2009-02-05                             | 72   | 2009-02 to 2009-12 |
  | 2009-08-18                             | ?    | not fetched        |
  | 2010-02-18                             | ?    | not fetched        |
  | 2010-03-24                             | 69   | 2010-03 to 2011-02 |
  | 2011-07-16                             | 75   | 2011-07 to 2012-11 |
  | 2011-10 to 2012-06 (6 captures)        | ?    | not fetched        |
  | 2012-08-25                             | 97   | 2012-08 to 2013-12 |
  | 2012-12-28                             | 102  | 2012-12 to 2013-12 |
  | 2013-09-21                             | 116  | 2013-09 to 2014-12 |
  | 2014-11-12                             | 132  | 2014-11 to 2016-01 |
  | 2015 (11 captures), fetched 2015-11-28 | 130  | 2015-11 to 2016-12 |
  | 2016-01-30, 2016-02-03                 | 111  | 2016-02 to 2016-12 |

  Full timestamp list: `asp/r00_web_archive_org.json`.

- The list mixes WCS registry events with other WSDC member events
  (UCWDC Worlds, country-dance weekends, IHSC). A crude slug match of the
  97 rows in the 2012-08 capture against registry series names with
  occurrences in the same window matched 54; the other 43 are renames
  (`DC Swing eXperience (DCSX)` versus `DC Swing Experience`), abbreviations
  (`Australian Open West Coast Swing Dance Champ's`), and non-WCS events.
  An alias table keyed by registry series id is required; name matching
  alone is not enough.
- A sibling page, `NonRegUpcomingEvents.asp` ("Other Member Sponsored
  Events"), lists non-registry member events, which is where trial events
  appeared before they earned registry status (Austin, Detonation, SinCity
  in the 2012-03 capture). Captures: 2010 (1), 2011 (1), 2012 (3), 2015 (5).
- Annual "All 2001/2002/2003 Events" pages existed only for those years.

### `worldsdc.com` in the archive, 2016 to 2023

Months with a 200 capture per path (CDX, status 200, event and calendar
paths only, `asp3/r05_web_archive_org.json`):

| Path                 | Months with captures                                                                |
| -------------------- | ----------------------------------------------------------------------------------- |
| `/events/`           | 2016-07, 08, 09, 11; 2019-03, 07 to 12; 2020-08 to 2021-03; 2023-02 to 05, 09 to 12 |
| `/event-list/`       | 2021-04 to 2022-12 (14 months)                                                      |
| `/print-event-list/` | 2017-06; 2019-03 to 2022-12 (about monthly); 2023-03, 10                            |
| `/event-calendar/`   | 2016-07 to 2017-01; 2017-06; 2019-03 to 2022-09                                     |
| `/events-map/`       | 2016-07 to 2017-07; 2019-03 to 2022-12                                              |
| `/wsdc-events/`      | 2021-04 to 2022-12                                                                  |

What each holds:

- `/events/` 2016-11-13: server-rendered rows with type ("Registry
  Event", 147 in that capture), venue address, contact. Dates print as
  `10th November, 2016 To 13th November, 2016`. This is a fourth date
  format for the calendar parser.
- `/event-list/` 2022-05-16: 50 rows, `May 19 - 22, 2022` to
  `Jan 19 - 22, 2023`, class `tr_events_load`, name linked to the event
  site, type line, `City, Region`, flag image. Same shape as today's
  `/events/`, so `journal/tools/collection/build_events.py` should parse it unchanged
  (**unverified** on a full capture).
- `/print-event-list/` 2017-06-06: a form (date range, event type,
  location) whose results are a POST; no rows in the capture. Today the
  live page renders no list at all.
- `/event-calendar/` 2017-06-06 and `/events-map/` 2017-01-24: JavaScript
  widgets; the captured HTML has no rows. The 2017 site also linked an
  `/events-download/` page; it has no 200 capture.
- So 2017 and 2018 stay empty in the archive. The 2016-11 capture lists
  into 2017 (how far is **unverified**; the horizon looked like about 12
  months) and 2019-03 captures start the other side.

### WSDC newsletters (2016-12 to 2024-05)

`worldsdc.com/newsletter/` links 28 PDF issues, Vol 1 (2016-12) to Vol 30
(2024-05); Vol 24 and Vol 28 are not linked. Checked issues:

| Issue           | Sidebar                                                                                                                                                          |
| --------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Vol 3, 2017-07  | "Registry Events July—September 2017", then "Upcoming Registry Events" on page 2                                                                                 |
| Vol 6, 2018-04  | "Registry Events April—June 2018"                                                                                                                                |
| Vol 8, 2018-10  | "Registry Events October—December 2018": 19 events with dates, `**` = information gathered from the web, not from the director                                   |
| Vol 30, 2024-05 | "Upcoming Registry Events", "WSDC Trial Events are shown in PURPLE" (colour is lost in text extraction; trial status needs the PDF's colour, **unverified** how) |

Each issue also has a "New Registry Events" box naming events approved
that quarter, which dates the start of a series' registry status. Names
are upper-cased and sometimes wrapped mid-word (`WORLD CHAPIONSHIPS`), so
they need the same alias table.

The quarterly cadence means the 2017-01 to 2019-03 hole is covered by
Vol 2 through Vol 9 if every issue carries the sidebar; four of four
checked issues do.

### The registry (`points.worldsdc.com`)

Unchanged from the design: 2,653 occurrences over 362 series in the
comparison dump, 1,886 since 2010; our own sweep holds 256 series with an
occurrence since 2010. The occurrence month is not the end month in a
measurable share of cases. Matching the 683 occurrences from 2019 on by
normalized name against calendar editions in the published `events`
table:

| Outcome                                           | Occurrences |
| ------------------------------------------------- | ----------- |
| calendar end month equals registry month          | 180         |
| calendar end month is the month before            | 24          |
| calendar end month is the month after             | 0           |
| same series, no edition within a month (coverage) | 312         |
| no calendar series of that name (alias needed)    | 167         |

The 24 late cases are nearly all European (Warsaw Halloween Swing,
Westie Gala, Nordic WCS Championships, Hungarian Open, Rolling Swing,
Paris Swing Classic). The likely cause is the month results were
entered, which the design already suspected. Rule for matching: a
registry occurrence in month M matches a dated edition of the same
series ending in M or M minus 1, never M plus 1. The
[backfill](../../../docs/reference/backfill.md#event-enumeration-for-history)
adjacent-month alias finding can become automatic with that direction.

The home page redirects to `/lookup2020`, a single-dancer search. There is
no event listing and no per-event page. The `wsdcregistry/v1` namespace on
`worldsdc.com` (`find.json`, `search.json`, `autocomplete.json`) is a proxy
to the same lookup.

### `worldsdc.com` today

- WordPress with Elementor and JetEngine. `wp-json/wp/v2/types` shows no
  event post type; the events table is rendered by a custom shortcode
  from data the REST index does not expose. Custom namespaces:
  `wsdcregistry/v1`, `moreevents` (`/moreevents/list.json`, the parties
  and classes page), `wsdcmap/v1` (map tiles only), and an admin
  `goo1-mcp/v1` namespace that we must never call.
- `/print-event-list/` and `/event-calendar-2/` return the page chrome
  and nothing else.
- Conclusion: the daily fetch of `/events/` remains the only live source,
  and it shows upcoming events only.

### Third parties checked and set aside

- `wcscalendar.wordpress.com` "Global West Coast Swing Event Calendar":
  its chronological view holds eleven events from 2013-07 to 2013-10.
  Too thin to matter.
- `westcoastswingonline.com/wsdc-events/`: a links page, no data.
- `danceconvention.net/eventdirector/en/eventsarchive`: 2013 on, already a
  platform index in the design.
- `nasde.net`: about twelve tour events a year; no historical schedule
  pages found.
- `wsdc.mechstack.dev/data.json`: the registry again; already the
  cross-check dump.

## How to build the list

1. **Series first.** One canonical series per registry `event.id`
   (`wsdc-<id>`), plus series that never earned a point (trial events
   that folded, cancelled series) created from listings. An
   `series_aliases.csv` maps every printed name (ASP page, calendar
   captures, newsletters, platforms) to a series id. Seed it from
   the crude matches above and review the rest by hand; the 2012-08
   capture alone needs about 40 rows.
2. **Editions from the registry.** One edition per occurrence since
   2010-01 (1,886), `held = true`, `date_precision = month`,
   `event_month` = registry month.
3. **Dates from listings, oldest first.** For each dated listing row
   (ASP captures 2009 to 2016, calendar captures 2016 on, `/event-list/`
   captures 2021 to 2022, newsletter sidebars 2017 to 2024, platform
   indexes), resolve the series through the alias table and attach start
   and end dates to the edition whose registry month is the end month or
   the month after. A listing with no registry occurrence becomes an
   edition with `held = unknown` (or `cancelled` when a calendar flag
   says so); it is published with that status, not dropped, so 2020 and
   2021 are honest.
4. **Type from the newest listing that states it.** "Registry Event",
   "Trial Event", "Member Activity". The ASP page states none; editions
   dated only from it keep the registry's word (a pointed occurrence is a
   registry event by definition).
5. **Coverage per year** is then: every pointed edition has a row; the
   share with day precision is limited only by the listing gaps below.

Expected gaps after all of this, to state in the card:

- 2011-03 to 2011-06 editions: month precision unless a platform or
  event site dates them (about 25 to 30 editions, **unverified** count).
- 2017-01 to 2019-02 editions that a newsletter sidebar did not print.
- Any edition whose printed name never resolves to a series id.
- Editions that awarded no points and appeared on no listing: invisible
  by construction, and there is no source that would show them.

## Fetch budget

Everything above is archive reads: 31 ASP captures, 11 non-registry
captures, about 60 `worldsdc.com` captures across five paths, and 28
newsletter PDFs from the live site (one request each, once). At the
playbook's 200-a-day archive budget this is one day of collection. The
newsletters are static uploads and can be fetched conditionally.

## Design changes this implies

- [backfill](../../../docs/reference/backfill.md): done on 2026-09-12. The event list
  is now phase 1, accepted per year before any score sheet is fetched;
  the old WSDC site, the other `worldsdc.com` paths, and the newsletters
  are event-list inputs; the month rule is automatic with the M or M
  minus 1 direction; `held` and `series_aliases.csv` are in the data
  model.
- [sources](../undated/initial-source-survey.md) and `docs/reference/sources/wsdc-calendar.md`:
  record the older domain, the five archived paths, and the REST
  finding.
- A new playbook, `docs/reference/sources/wsdc-newsletters.md`, for the PDFs.
- `series_aliases.csv` alongside `event_aliases.csv` in
  [repository layout](../../../docs/how-it-works/code-map.md).

## Things still to verify

- The `*` marker on ASP dates, and whether the ASP page's default window
  was fixed or set by the archive's crawler.
- The horizon of the 2016-11 `/events/` capture and of the 2009-08 and
  2010-02 ASP captures (they decide whether 2010-01 and 2017 are covered).
- Whether every newsletter issue carries the sidebar, and how to recover
  the purple trial-event colour from the PDF.
- Whether `journal/tools/collection/build_events.py` parses the 2021 to 2022
  `/event-list/` captures without change.
- Vol 24 and Vol 28 newsletters: whether they exist under other URLs.
