# Historical backfill from 2010-01-01

Status: draft v0.1, 2026-09-11. Owner of the backfill contract: this
document. Evidence: `research/wayback-coverage-2026-09-11.md`.

`swingset` tabulates events and results from **2010-01-01** onward. Most
of that history is no longer on the sites that first published it, and
what is still there should not be re-crawled. So history is read from
the Internet Archive's Wayback Machine first, from the WSDC registry for
the event list, and from the origin sites only for gaps the archive
cannot fill. This document says what the start date means, where each
year's data comes from, how the Wayback transport works, how it stays
polite, and how the work is ordered and measured.

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

## What exists for each era (verified 2026-09-11)

| Era          | Event list                                                                                          | Score sheets                                                                                                                                                                  | Notes                                     |
| ------------ | --------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------- |
| 2010 to 2012 | registry occurrences (80 to 91 a year); no calendar captures                                        | Step Right Solutions round pages in the archive for 4 to 6 US West Coast events a year; event-site PDFs in the archive where they were linked                                 | Most events will be registry-only         |
| 2013 to 2016 | registry (106 to 135 a year); calendar captures only from late 2016                                 | Step Right for 13 to 17 events a year (US West Coast, Canada, France, Singapore); event-site PDFs; EEPro's origin still serves old slugs but the archive has none before 2018 |                                           |
| 2017 to 2020 | registry (146 to 153 a year, 31 in 2020); calendar captures in 2019 and 2020, none in 2017 and 2018 | EEPro archive from 2018 (8 to 16 slugs a year); DCN event pages from 2017; Step Right until 2019; event-site PDFs                                                             | Ask the EEPro operator for pre-2018 slugs |
| 2021 to 2025 | registry; calendar captures (2 months in 2021, none in 2022, 4 to 5 a year after)                   | scoring.dance archive from 2021-06; EEPro and DCN archives; WDR from 2022; our own archive from 2026-09                                                                       |                                           |

The Wayback Machine holds no copy of `worldsdc.com` before 2016-10-30.
Whether the WSDC calendar lived on another domain earlier is
**unverified**. Step Right Solutions' origin returns empty pages today;
the archive is the only copy of its 1,685 round pages.

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

Discovery for history extends [scheduling](scheduling.md#discovery)
with three sources of event rows, applied in this precedence:

1. **Calendar captures.** Each archived capture of
   `worldsdc.com/events/` is a `wsdc_calendar.events` snapshot fetched
   through the Wayback transport, one per month with a capture, oldest
   first. Rows become calendar observations exactly as live rows do, so
   `project_map` needs no new input kind. Coverage has holes (no
   captures in 2017, 2018, and 2022), which the next two sources fill.
2. **Platform indexes from the archive.** EEPro `event.php` captures,
   scoring.dance `recent` and sitemap captures, DCN `eventsarchive`
   captures, and the Step Right events index. These produce
   `source_events` with the platform's own dates.
3. **Registry occurrences.** A new projection, `project/registry_events.py`
   extended, seeds one `events` row per registry occurrence (series id
   plus month) on or after the start date that no calendar or platform
   row already matches by series slug and month. The row has
   `series_id = wsdc-<id>`, `event_id = <yyyy-mm>-<series slug>`,
   `name` from the registry, `wsdc_status = registry`,
   `date_precision = month`, null `start_date` and `end_date`,
   `event_month` set, `website` from the registry URL, and city and
   country parsed from the registry location (dirty; kept raw as well).
   When a calendar or platform row later supplies dates for the same
   slug and month, the row is upgraded to `date_precision = day` under
   the same `event_id`, so ids never change.

The registry month is believed to be the month results were reported,
not necessarily the end-date month ([data model](data-model.md#identifiers),
**unverified**). When they differ, the same edition appears as two
events one month apart. The map unit detects a registry-seeded event
whose slug matches a dated event in the adjacent month and opens an
`event_alias` finding with a suggested `event_aliases.csv` row; it does
not merge automatically. The bootstrap sweep answers how often this
happens; if it is common, the rule becomes automatic and is recorded
here.

## Data model changes

Schema is pre-1.0 until M6 closes, so these land without ceremony.

| Table                                | Change                                                                                                                                                                                                                                                                  |
| ------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `events`                             | `start_date`, `end_date` nullable; new `event_month` (string `yyyy-mm`, not null), `date_precision` enum `day` or `month`, `coverage_tier` enum from the table above, `history_source` list (which of calendar, platform, registry, steprightsolutions named the event) |
| `snapshots` (internal and published) | `via` enum `origin`, `wayback`, `manual`; `captured_at` timestamp (Memento datetime for `wayback`, equal to `fetched_at` otherwise); `archive_url`; `observed_at` = `captured_at`                                                                                       |
| `archive_captures` (internal, new)   | `source`, `url`, `timestamp`, `digest`, `status`, `mimetype`, `length`, `queried_at`, `cdx_query_id`; the CDX index we hold for each source, so selection can be redone without asking the archive again                                                                |
| `coverage` (published, new)          | one row per `year`, `source`, `via`: events, contests, rounds, entries, and events per tier; built from current state; this is the card's coverage table                                                                                                                |
| `contests.source` / `source` enum    | new value `steprightsolutions`                                                                                                                                                                                                                                          |
| `rounds.callback_legend`             | Step Right uses `legacy_3` (marks `1`, `2`, `3`)                                                                                                                                                                                                                        |
| `judges`                             | round rosters with `anonymous = true` marks: Step Right names the panel but shuffles the columns, so marks attach to `anon-<n>` judge ids while named judges are recorded on the round with `marks_attributed = false`                                                  |

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
  already does ([long tail](../docs/sources/long-tail.md)).

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
playbook `docs/sources/wayback-machine.md` section 6 and
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
| `scoring.dance`                 | One event per cycle from the sitemap ids the archive lacks, newest first. The site starts in 2021; nothing older exists there.                                                                                                                                                                                                       |
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
- Order: newest year first; within a year, sources by number of events
  the archive holds for that year (largest first) so each week of
  backfill publishes the most rows; within a source, event pages before
  round pages, so gaps are known before rounds are spent on them.
- Priority 6, below registry work. A cycle takes backfill fetches only
  when no other watch is due and the wall-clock budget has more than two
  minutes left. Calendar captures are the exception: they are index work
  (priority 2) because everything downstream needs the event list.
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

| WP                             | Scope                                                                                                                                                                                                             | Done when                                                                                                                                               |
| ------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------- |
| WP11 Wayback transport         | `fetch/wayback.py`: CDX paging into `archive_captures`, capture selection, `id_` fetch through the gate, Memento headers, `via`, `captured_at`, `observed_at` in conflict and ownership rules; the `sealed` state | offline tests with recorded CDX and body fixtures; one real EEPro 2019 event read end to end from the archive; `doctor` shows the archive host's budget |
| WP12 History events            | `history_start`; nullable dates, `event_month`, `date_precision`, `coverage_tier`, `history_source`; registry-seeded events; adjacent-month alias finding; `coverage` table and card section                      | every registry occurrence since 2010 has an `events` row; coverage table published; the bootstrap sweep's month-vs-date question answered               |
| WP13 Calendar history          | calendar captures as index snapshots, oldest first                                                                                                                                                                | every calendar capture month is parsed; 2019 to 2026 events carry day precision where a capture listed them                                             |
| WP14 Step Right Solutions      | `sources/steprightsolutions/`: index, event, round parsers with `legacy_3` marks and anonymous judge columns; the playbook's open items answered from fixtures                                                    | all 2009 to 2016 events with round pages parse or carry a finding                                                                                       |
| WP15 Platform archive backfill | EEPro, scoring.dance, DCN, WDR captures newest first; gap findings                                                                                                                                                | the coverage table shows archive versus origin counts per year                                                                                          |
| WP16 Origin gap fill           | EEPro operator conversation about pre-2018 slugs; scoring.dance and DCN gap rules; event-site override rows from CDX PDF hits through the review queue                                                            | no origin request is made for a page the archive holds; gaps listed in the card                                                                         |

Order: WP11, WP12, WP13 together (they are M6's foundation), then WP15
because it publishes the most rows soonest, then WP14, then WP16. WP14
can run in parallel with WP15 since it touches only new modules.

## Things to verify

- Step Right prelims: how promotion is marked; whether `2` has
  sub-values; which bib a finals row shows for a Jack and Jill couple.
- Whether DCN `roundscores/*.pdf` files are in the archive at all.
- The meaning of the `X-RL` and `X-NA` response headers from the archive.
- The Internet Archive's terms wording, read by hand (issue #20).
- How often the registry month differs from the end-date month (issue #22).
- Whether an older WSDC domain carried the calendar before 2016.
- EEPro: which slugs before 2018 the origin still serves (from the
  operator, not by probing; issue #21).
