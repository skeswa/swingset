# Historical backfill from 2010-01-01

Backfill means collecting older results. This page owns the history start date, how events are counted, and when to use archived pages or the original website. A known event is not proof that all its result sheets have been found.

[Reference index](README.md)

## On this page

- [Events first](#events-first)
- [The start-date rule](#the-start-date-rule)
- [What exists for each era (verified 2026-09-11 and 2026-09-12)](#what-exists-for-each-era-verified-2026-09-11-and-2026-09-12)
- [Event enumeration for history](#event-enumeration-for-history)
- [Data model changes](#data-model-changes)
- [The Wayback transport](#the-wayback-transport)
- [Scheduling](#scheduling)
- [Identity linking for history](#identity-linking-for-history)
- [Personal data](#personal-data)
- [Work packages](#work-packages)
- [Things to verify](#things-to-verify)

Status: draft v0.2, 2026-09-12. Owner of the backfill contract: this
document. Evidence: `journal/investigations/2026/wayback-coverage-2026-09-11.md` and
`journal/investigations/2026/event-list-sources-2026-09-12.md`.

`swingset` tabulates events and results from **2010-01-01** onward. Most
of that history is no longer on the sites that first published it, and
what is still there should not be re-crawled. So history is read from
the Internet Archive's Wayback Machine first, from the WSDC registry for
the event list, and from the origin sites only for gaps the archive
cannot fill. This document says what the start date means, why the
event list is finished before any score sheet, where each year's data
comes from, how the Wayback transport works, how it stays polite, and
how the work is ordered and measured.

## Events first

The event list is the spine of every other table: contests, rounds,
entries, placements, and registry links all hang off an `events` row.
A wrong or missing row there is wrong everywhere below it, and a row
added later changes ids downstream. So backfill runs in two phases per
year, and phase 2 never starts for a year until phase 1 is accepted for
it.

**Phase 1, the event list.** One `events` row per WSDC event edition
that ended in the year, with these four properties:

| Property             | Source of truth                                                                                | Acceptance                                                                                                       |
| -------------------- | ---------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------- |
| The edition was held | a registry occurrence (points were awarded); failing that, a parsed score sheet                | every registry occurrence in the year has exactly one `events` row                                               |
| Start and end dates  | a listing published before or during the event (calendar, old WSDC site, newsletter, platform) | every edition a listing dates carries `date_precision = day`; the rest are `month` and counted in the card       |
| Series identity      | the registry series id, through `series_aliases.csv` for every printed name                    | no listing row in the year is left unresolved; each unresolved name is a finding until a human adds an alias     |
| Status               | listing type and flags (registry, trial, cancelled, hiatus) and newsletter approval notices    | every listing-only edition (no occurrence) carries `held = listed` or `held = cancelled`, never silently dropped |

Acceptance is per year, recorded in the `coverage` table as
`events_accepted = true`, and is the owner's call after the findings for
that year are empty. Editions that awarded no points and appeared on no
listing cannot be found by any source and are out of scope by
construction; the card says so.

**Phase 2, score sheets.** Platform and event-site backfill for the
year, as the rest of this document describes. Every sheet attaches to an
accepted `events` row; a sheet whose event has no row opens a finding
instead of creating one, because a row created from a sheet has no
registry identity and would need renaming later.

Why this order: the registry sweep showed that 138,280 of 159,115
registry placements since 2010 had no `event_id` and that no `events`
row existed for 2010 to 2017
(`journal/investigations/2026/missing-data-2026-09-12.md`). Filling sheets under those
conditions would attach results to rows that later merge, split, or
change id. Fixing the list first makes every later step attach to a
stable key.

## The start-date rule

- An event is in scope when its end date, or its registry month when no
  date is known, is on or after 2010-01-01. `events`, `contests`,
  `rounds`, `entries`, `heats`, `judges`, callbacks, marks, and
  `placements` are built only for in-scope events.
- The registry mirror is dancer-centric and already complete back to 1991. `dancers` and `registry_placements` are published whole. A
  registry placement before 2010 keeps a null `event_id`; nothing before
  2010 gets an `events` row. The card says this plainly so nobody reads
  the absence as missing data.
- The start date is a constant in `config/sources.toml`
  (`history_start = 2010-01-01`); the same date is the code default in
  `swingset.model.history.HISTORY_START`, so an absent key means 2010.
  The build refuses any `events` row that ended before it
  ([build](build.md)), the manifest records `history_start`, and the
  card states the rule. Moving it earlier is a config change
  plus a backfill run; moving it later is a schema-visible change
  ([publishing](publishing.md#commit-strategy)) because rows disappear.

Why 2010: registry data before then is thin (74 occurrences in 2009,
under 40 a year before 2003), the earliest archived round sheets we can
find start in 2009, and the owner set the date.

## What exists for each era (verified 2026-09-11 and 2026-09-12)

| Era          | Event list                                                                                                                                                                | Score sheets                                                                                                                                                                                                 | Notes                                                                              |
| ------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------- |
| 2010 to 2016 | registry occurrences (80 to 135 a year); `swingdancecouncil.com` "Member Registry Events" captures, 31 between 2009-02 and 2016-02, each listing the next 11 to 16 months | Step Right Solutions round pages in the archive for 4 to 17 events a year; event-site PDFs in the archive where they were linked; EEPro's origin still serves old slugs but the archive has none before 2018 | Day precision for nearly every edition; the 2011-03 to 2011-06 patch is month-only |
| 2016-07 on   | `worldsdc.com/events/` captures (4 months in 2016)                                                                                                                        |                                                                                                                                                                                                              | 2016 rows use a fourth date format, `10th November, 2016 To 13th November, 2016`   |
| 2017 to 2018 | registry (148, 146); WSDC newsletter sidebars, one issue per quarter; the 2016-11 calendar capture reaches into 2017                                                      | EEPro archive from 2018 (8 to 16 slugs a year); DCN event pages from 2017; Step Right until 2019; event-site PDFs                                                                                            | No archived `worldsdc.com` page with rows in either year                           |
| 2019 to 2021 | registry (153, 31, 26); calendar captures; newsletters                                                                                                                    | as above                                                                                                                                                                                                     | Cancellation flags decide `held`                                                   |
| 2022         | registry (92); `/event-list/`, `/print-event-list/`, `/event-calendar/`, `/events-map/` captures (14 months, 2021-04 to 2022-12); newsletters                             | scoring.dance archive from 2021-06; EEPro and DCN archives; WDR from 2022                                                                                                                                    | `/events/` itself has no 2022 capture                                              |
| 2023 to 2025 | registry; calendar captures (4 to 5 a year); newsletters to 2024-05; our own daily fetch from 2026-09                                                                     | as above; our own archive from 2026-09                                                                                                                                                                       |                                                                                    |

The Wayback Machine holds no copy of `worldsdc.com` before 2016-10-30.
Before that the list lived at
`swingdancecouncil.com/ActiveServerPages/UpcomingEvents.asp`, which
returned 404 from 2016-03 and redirects since 2017. Step Right
Solutions' origin returns empty pages today; the archive is the only
copy of its 1,685 round pages.

Expected outcome, stated in the card as coverage tiers per event:

| Tier              | Meaning                                                                   |
| ----------------- | ------------------------------------------------------------------------- |
| `sheets_complete` | every contest the event's index lists has every round it lists, parsed    |
| `sheets_partial`  | some rounds or contests parsed; the rest are gaps                         |
| `index_only`      | the event was found on a platform or calendar but no sheet parsed         |
| `registry_only`   | known only from registry placements: series, month, finalists with points |

Registry-only is the floor, not a failure. For 2010 to 2015 it will be
the most common tier.

## Event enumeration for history

This is phase 1. Discovery for history extends
[scheduling](scheduling.md#discovery) with these inputs, applied in
this order for each year, oldest year first:

1. **Series.** One canonical series per registry `event.id`
   (`series_id = wsdc-<id>`, name and website from the registry).
   `series_aliases.csv` maps every printed name seen in any listing
   (old WSDC site, calendar captures, newsletters, platform indexes) to
   a series id, since the registry prints today's name for every year
   and listings abbreviate, rename, and upper-case. A listing name with
   no alias and no exact normalized match opens a `series_alias` finding
   with the suggested row; nothing is merged by guess. A series that
   never earned a point (a trial event that folded, a cancelled series)
   is created from the listing with `series_id = listed-<slug>` and
   upgraded to `wsdc-<id>` if the registry ever names it.
2. **Registry occurrences.** `project/registry_events.py` seeds one
   `events` row per occurrence (series id plus month) on or after the
   start date: `event_id = <yyyy-mm>-<series slug>`, `held = held`,
   `wsdc_status = registry`, `date_precision = month`, null
   `start_date` and `end_date`, `event_month` set, `website` from the
   registry URL, and city and country parsed from the registry location
   (dirty; kept raw as well). This is the floor: 1,886 rows since
   2010-01.
3. **Dated listings**, each a snapshot read through the Wayback
   transport or from the live site, oldest first:
   - `swingdancecouncil.com/ActiveServerPages/UpcomingEvents.asp`
     captures (2009 to 2016), parser `swingdancecouncil.events`: date
     text in three shapes (`Aug. 23 - 26*, 2012`, `Nov. 29-Dec. 2, 2012`,
     `July TBD, 2013`), name, `City, ST` or `City, Country`, contacts. No
     type column. Its sibling `NonRegUpcomingEvents.asp` lists
     non-registry member events and is read the same way with
     `wsdc_status = trial` as the default.
   - `worldsdc.com` captures of `/events/` and, for months it lacks,
     `/event-list/`, `/print-event-list/`, `/event-calendar/`, and
     `/events-map/`, all parsed by `wsdc_calendar.events`, which gains
     the 2016 date format. Captures whose HTML holds no rows (2017
     forms and map widgets) are recorded as empty, not as errors.
   - WSDC newsletter PDFs (`worldsdc.com/newsletter/`, 28 issues,
     2016-12 to 2024-05), parser `wsdc_newsletter.events`: the
     "Upcoming Registry Events" sidebar (name, dates) and the "New
     Registry Events" box (name, approval quarter). Trial events are
     marked by colour only, so `wsdc_status` from a newsletter is
     `registry` unless the colour is recovered (**unverified** how).
   - Platform indexes from the archive: EEPro `event.php`, scoring.dance
     `recent` and sitemap, DCN `eventsarchive`, and the Step Right events
     index, as `source_events` with the platform's own dates.
     Each listing row resolves to a series through step 1 and attaches
     to the occurrence whose registry month is the listing's end month
     or the month after it (the rule below). The row is upgraded to
     `date_precision = day` under the same `event_id`, so ids never
     change. Later listings override earlier ones for dates and place,
     by `observed_at`.
4. **Listing-only editions.** A listing row with no occurrence within
   that window becomes an `events` row with `held = listed`, or
   `held = cancelled` when the calendar flag or a hiatus name says so.
   It keeps the listing's dates and type. These rows are published; they
   are what makes 2020 and 2021 honest.

**The month rule.** The registry month is the end month for 180 of 204
occurrences since 2019 that match a dated calendar edition by name, and
the month after for the other 24 (nearly all European events, so the
month results were entered, **unverified** cause). None was earlier. So
an occurrence in month M matches a dated edition of the same series
ending in M or M minus 1, never M plus 1, and the match is automatic. A
series with two dated editions inside that window opens an
`event_alias` finding rather than choosing. `event_aliases.csv` remains
the override for anything the rule gets wrong.

## Data model changes

Schema is pre-1.0 until M6 closes, so these land without ceremony.

| Table                                | Change                                                                                                                                                                                                                                                                                                                                                        |
| ------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `events`                             | `start_date`, `end_date` nullable; new `event_month` (string `yyyy-mm`, not null), `date_precision` enum `day` or `month`, `held` enum `held`, `listed`, `cancelled`, `coverage_tier` enum from the table above, `history_source` list (which of `registry`, `calendar`, `swingdancecouncil`, `newsletter`, `platform`, `steprightsolutions` named the event) |
| `series_aliases.csv` (override, new) | `printed_name`, `series_id`, `source`, `note`; reviewed by hand, never written by the pipeline; lives beside `event_aliases.csv` ([repository layout](../how-it-works/code-map.md))                                                                                                                                                                           |
| `coverage`                           | also `events_accepted` per year (phase 1 sign-off), `events_day_precision`, `events_listed_only`                                                                                                                                                                                                                                                              |
| `snapshots` (internal and published) | `via` enum `origin`, `wayback`, `manual`; `captured_at` timestamp (Memento datetime for `wayback`, equal to `fetched_at` otherwise); `archive_url`; `observed_at` = `captured_at`                                                                                                                                                                             |
| `archive_captures` (internal, new)   | `source`, `url`, `timestamp`, `digest`, `status`, `mimetype`, `length`, `queried_at`, `cdx_query_id`; the CDX index we hold for each source, so selection can be redone without asking the archive again                                                                                                                                                      |
| `coverage` (published, new)          | one row per `year`, `source`, `via`: events, contests, rounds, entries, and events per tier; built from current state; this is the card's coverage table                                                                                                                                                                                                      |
| `contests.source` / `source` enum    | new value `steprightsolutions`                                                                                                                                                                                                                                                                                                                                |
| `rounds.callback_legend`             | Step Right uses `legacy_3` (marks `1`, `2`, `3`)                                                                                                                                                                                                                                                                                                              |
| `judges`                             | round rosters with `anonymous = true` marks: Step Right names the panel but shuffles the columns, so marks attach to `anon-<n>` judge ids while named judges are recorded on the round with `marks_attributed = false`                                                                                                                                        |

Evidence time. [Architecture](architecture.md#observations-and-projections)
resolves conflicts by later `fetched_at`. For history that is wrong: a
capture from 2016 fetched by us in 2027 must not outrank a live fetch
from 2026. Conflict resolution and current-observation ownership
([parsing](parsing.md#contract)) therefore compare `observed_at`, which
is the capture time for archive snapshots and the fetch time otherwise.
Snapshot id still breaks ties.

## The Wayback transport

`fetch/wayback.py` is a transport inside the fetch layer. Source adapters
never know whether a body came from the archive; they see the original
URL and an `observed_at`.

### Index: CDX queries

- One query per source, per capture year, per URL prefix listed in the
  playbook, using `output=json`, `filter=statuscode:200`,
  `fl=timestamp,original,digest,mimetype,length`, `from=<yyyy>`,
  `to=<yyyy>`, `collapse=digest`, and `page=<n>` after a
  `showNumPages=true` probe. Never a whole-history query over a large
  prefix: those took 8 to 24 s of the archive's time and three timed out
  at 30 s on 2026-09-11.
- CDX rows are stored in `archive_captures` with the query id. A year's
  query is repeated only when the source's playbook says the year is
  still being captured (the current and previous calendar year), on a
  90-day clock; older years are queried once.
- Event-site prefixes (registry and calendar `website` values, 229 of
  362 series have one) are queried with `filter=mimetype:application/pdf`
  plus a second query for HTML whose path contains `result`, `score`,
  `callback`, `prelim`, or `final`, for the event year and the next.
  Hits are not fetched automatically; they become suggested
  `source_urls.csv` rows in the review queue, as the live link scan
  already does ([long tail](sources/long-tail.md)).
- Filtered event-site query receipts use `source = event_sites` in
  `archive_queries` and `archive_captures`. They must not use a platform
  source such as `eepro`: origin fallback treats completed platform queries
  as searches over all 200-status captures, and a PDF or path filter cannot
  prove that other archive copies are absent.

### Selecting a capture

For each original URL with captures:

1. Candidates are 200-status captures, distinct by digest.
2. Prefer the latest capture made at least 30 days after the event's
   end date (or month end); otherwise the latest capture. WSDC rules let
   results change for 30 days, so a later capture is the corrected one.
3. Fetch it. If `extract` fails or the parse is incomplete (fewer
   contests or rounds than the event page lists, empty tables), try the
   next-latest distinct capture, at most three per URL, then record a
   gap finding on the source event.
4. A capture that parses is final. Captures are immutable, so a fetched
   `(url, timestamp)` is never fetched again; the watch moves to `sealed`
   (below).

### Fetching a body

- URL: `https://web.archive.org/web/<timestamp>id_/<original url>`. The
  `id_` flag returns the original bytes without the toolbar or rewritten
  links. Redirects inside the archive (a capture stored under a
  different timestamp) are followed through the gate, at most 3 hops,
  and the final Memento timestamp is what is stored.
- Stored under the original URL with `via = wayback`, `archive_url`, and
  `captured_at` from `memento-datetime`. Headers kept: `memento-datetime`,
  `x-archive-src`, `link` (timemap and neighbouring mementos), and every
  `x-archive-orig-*` header, which is the origin's response at capture
  time; the origin's `Last-Modified` and `ETag` from there are stored in
  the usual validator columns so a later origin fetch can be conditional.
- Extraction, fingerprinting, parsing, and archiving are the live path.
  The body is always archived (there is no previous fingerprint to
  compare against) unless a blob with the same hash exists.
- Wayback bodies are already gzip-compressed in transit when the origin
  was; `Accept-Encoding: gzip` stays as it is everywhere.
- Never Save Page Now, never the availability API in the pipeline, never
  Common Crawl in v1. Common Crawl stays a documented option for gaps.

### Host settings and etiquette

The archive is a nonprofit library serving everyone; it is treated as
the most sensitive host we have, not the least. Settings live in the
playbook `docs/reference/sources/wayback-machine.md` section 6 and
`config/hosts.toml`: 10 s gap (six requests a minute), 200 requests a
day to start, one in flight, the default 429 and 503 rules, a 120 s
timeout for CDX and 30 s for bodies, and backfill runs only when nothing
else is due. Raising the daily budget to 400 is an operator decision
after a week with no throttling, recorded in the playbook.

`web.archive.org/robots.txt` is 404, which our rule reads as
unrestricted, and the archive publishes no rate limit. The Internet
Archive's terms page needs JavaScript and was not read by the pipeline;
the owner reads it by hand before M6 starts and records the date in the
playbook. The commonly quoted clause that access is granted for
scholarship and research purposes is **unverified** wording; this
project is research and a non-commercial dataset, and the card says so.

### Origin fallback for gaps

A gap is an in-scope event, contest, or round that the archive does not
hold or holds only incompletely. Origin backfill follows the host's own
playbook and these limits:

| Host                            | Rule                                                                                                                                                                                                                                                                                                                                 |
| ------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `eepro.com`                     | Old slugs are still served (`/results/liberty2018/` returned 200 on 2026-09-11) but `event.php` lists only 2024 on and year indexes do not exist. Ask the operator for the slug list, or for the API, before fetching anything older than the archive holds. Then one event per cycle, conditional GETs, within the playbook budget. |
| `scoring.dance`                 | One event per cycle from the sitemap ids the archive lacks; admit new events newest first and rotate already-waiting events under H14. The site starts in 2021; nothing older exists there.                                                                                                                                          |
| `danceconvention.net`           | `eventsarchive:loadyear?year=<yyyy>` once per year of history, then at most one event page per day, PDFs only for rounds whose archive copy is missing.                                                                                                                                                                              |
| `scores.worlddanceregistry.com` | No index exists; overrides only. Nothing before 2022.                                                                                                                                                                                                                                                                                |
| `steprightsolutions.com`        | Never. The origin is dead; every gap stays a gap.                                                                                                                                                                                                                                                                                    |
| Event sites                     | Only when the site is alive, robots allows it, and the URL came from an override row. Default host settings.                                                                                                                                                                                                                         |

Origin backfill for one host never exceeds one event per cycle, and DCN
one per day. It is priority 6 like archive backfill, but archive
backfill of the same source runs first so an origin request is never
spent on a page the archive has.

## Scheduling

- Backfill work is a watch with `state = backfill` and `archive_url`
  set, exactly as [scheduling](scheduling.md#watch-states-and-intervals)
  reserves. Watch ids hash the original URL, so a page later seen live
  and a page seen in the archive are one watch.
- Admit new work newest year first; within a year, prefer sources with
  more known archived events. Read an event's index before its listed rounds.
  Once admitted, unfinished events receive the bounded turns and protected
  service in [scheduling](scheduling.md#event-completion). Newer arrivals
  cannot repeatedly displace older waiting events. Origin's one-event-per-cycle
  limit, DCN's daily event limit, and archive-first selection still apply;
  rotate at the next eligible cycle when a host limit prevents an in-cycle turn.
- Backfill keeps priority 6 as a policy label, but receives the reserved
  old-work acquisition share while other classes have due work. The former
  idle-only rule is superseded by H14's host and cycle allocations. Event-list
  captures (old WSDC site, calendar paths, newsletters) retain their index-work
  exception (priority 2), and phase 1 still precedes phase 2 for each year.
  The original estimate of about 130 archive reads and 28 PDFs is a planning
  estimate, not a measured completion guarantee.
- Phase 2 watches for a year are created only when `events_accepted`
  is true for that year. Until then the year's sheets are not fetched,
  however cheap they are.
- New state `sealed`: a watch whose event ended more than two years ago,
  or whose origin host is marked dead in its playbook, is never fetched
  again after a successful parse. Reparsing from the archive is
  unaffected. This replaces the 90-day `archived` clock for old events
  and cuts steady-state load for every source.
- Budget arithmetic: roughly 1,124 EEPro, 3,155 scoring.dance, 1,188 DCN
  event pages plus their PDFs, 1,873 Step Right, 34 WDR, and about 40
  calendar captures, plus a few hundred CDX pages, is about 8,000 archive
  requests. At 200 a day that is six weeks of quiet days; at 400, three.
  DCN PDFs in the archive are **unverified** and could add a thousand.

## Identity linking for history

Nothing new is needed in the linker. Registry confirmation
([identity linking](identity-linking.md#retroactive-correction)) works
backwards as well as forwards because the mirror holds every pointed
placement since 1991; a 2012 finalist with a unique normalized name and a
matching registry placement is `confirmed` on the first link pass.
Division consistency uses the dancer's level on the event date, derived
from placements before that date, not today's level. Newcomers of 2012
who never earned a point stay `unmatched`, as they do today.

Judges on Step Right pages are named but their marks are anonymous.
Judge rows are linked as usual; marks reference `anon-<n>`.

## Personal data

Backfilling adds sixteen years of names, most of them of people who no
longer compete. The posture in [ethics and legal](ethics-and-legal.md)
holds unchanged: the sources were public, the registry publishes the
same names for the same placements, and `suppressions.csv` removes a
person from every year in one publish cycle. The card states the start
date and that historical rows come from the Internet Archive, with
`snapshots.via` and `archive_url` on every row's provenance so a request
can name the exact capture. Archived pages carry the original site's
copyright; the facts-not-sheets position is unchanged.

## Work packages

| WP                             | Scope                                                                                                                                                                                                                                                    | Done when                                                                                                                                                |
| ------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------- |
| WP11 Wayback transport         | `fetch/wayback.py`: CDX paging into `archive_captures`, capture selection, `id_` fetch through the gate, Memento headers, `via`, `captured_at`, `observed_at` in conflict and ownership rules; the `sealed` state                                        | offline tests with recorded CDX and body fixtures; one real EEPro 2019 event read end to end from the archive; `doctor` shows the archive host's budget  |
| WP12 History events            | `history_start`; nullable dates, `event_month`, `date_precision`, `held`, `coverage_tier`, `history_source`; series and `series_aliases.csv`; registry-seeded events; the automatic month rule; `coverage` table with `events_accepted` and card section | every registry occurrence since 2010 has exactly one `events` row; coverage table published; unresolved names surface as findings                        |
| WP13 Event-list history        | old WSDC site captures (`swingdancecouncil.events` parser), calendar captures of all five `worldsdc.com` paths including the 2016 date format, newsletter PDFs (`wsdc_newsletter.events` parser), all as index snapshots, oldest first                   | every capture and issue is parsed or recorded empty; each year 2010 to 2026 carries day precision wherever a listing dated it; phase 1 accepted per year |
| WP14 Step Right Solutions      | `sources/steprightsolutions/`: index, event, round parsers with `legacy_3` marks and anonymous judge columns; the playbook's open items answered from fixtures                                                                                           | all 2009 to 2016 events with round pages parse or carry a finding                                                                                        |
| WP15 Platform archive backfill | EEPro, scoring.dance, DCN, WDR captures admitted newest first; bounded turns for waiting events; gap findings                                                                                                                                            | the coverage table shows archive versus origin counts per year                                                                                           |
| WP16 Origin gap fill           | EEPro operator conversation about pre-2018 slugs; scoring.dance and DCN gap rules; event-site override rows from CDX PDF hits through the review queue                                                                                                   | no origin request is made for a page the archive holds; gaps listed in the card                                                                          |

Order within this document: WP11, then WP12 and WP13 together; these three are phase 1 and
M6's foundation, and no phase 2 package starts for a year until that
year's event list is accepted. Then WP15 because it publishes the most
rows soonest, then WP14, then WP16. WP14 can run in parallel with WP15
since it touches only new modules. Parsers for WP14 to WP16 may be
written earlier; their watches are not.

## Things to verify

- Step Right prelims: how promotion is marked; whether `2` has
  sub-values; which bib a finals row shows for a Jack and Jill couple.
- Whether DCN `roundscores/*.pdf` files are in the archive at all.
- The meaning of the `X-RL` and `X-NA` response headers from the archive.
- The Internet Archive's terms wording, read by hand (issue #20).
- The month rule was measured on 2019 to 2026 only; whether the
  one-month-late share holds for 2010 to 2018 (issue #22).
- The `*` after some dates on the old WSDC site, and whether that
  page's default window was fixed or chosen by the crawler.
- The horizon of the 2016-11 calendar capture and of the 2009-08 and
  2010-02 old-site captures, which decide whether 2010-01 and 2017 are
  fully dated.
- Whether every newsletter issue carries the sidebar (four of four
  checked do), how to recover the purple trial-event marking from the
  PDF, and whether Vol 24 and Vol 28 exist under other URLs.
- Whether `journal/tools/collection/build_events.py` parses the 2021 to 2022
  `/event-list/` captures unchanged.
- EEPro: which slugs before 2018 the origin still serves (from the
  operator, not by probing; issue #21).
