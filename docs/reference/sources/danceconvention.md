# danceconvention.net (DCN)

danceconvention.net runs registration and scoring for many events outside
North America and some inside it. Its results tab lists every contest, round
and placement of an event; per-round score PDFs add bibs and judges' marks.
The `dcn` source reads the archive and the results tabs of WSDC events. It
ships disabled; see [rollout](#14-rollout).

[All sources](README.md) · [Shared fetching rules](../fetching.md)

## 1. Status

Verified 2026-10-15 ([investigation](../../../journal/investigations/2026/dcn-results-source-2026-10-15.md)).
The adapter is `src/swingset/sources/dcn/live.py`, registered as `dcn` with
the page kinds `dcn.archive` and `dcn.event`
([D-0255](../../../journal/decisions/0255-collect-danceconvention-net-finals-from-the-results-payload.md)).
`config/sources.toml` has `enabled = false` until the rollout.

The operator relationship is not known to be personal; treat the operator as
a stranger and be stricter, not looser. `robots.txt` (at
`/eventdirector/robots.txt` after a redirect from `/robots.txt`) disallows
registration flows, `index:retrievenewsfeed`, `eventpage:selectresultscontestrow`,
`eventpage:selectsignupscontestrow`, `eventpage:invitepartner`,
`systemsignup.systemsignupform` and `eventpage.schedulecalendar:*` in every
locale. The archive, the results tab and `roundscores/*.pdf` are allowed. It
was unchanged between 2026-09-08 and 2026-10-15. No terms page found
(2026-09-04).

## 2. What it gives

The results tab gives, per contest and round, each ranked dancer's role, name,
city, country, rank and, for ties, `interval` (the number of tied places).
Finals and couple rounds add the partner. A preliminary or semifinal lists
only the dancers that round eliminated, with their overall rank; dancers
called back appear in the next round. No bibs, marks or WSDC IDs.

Score PDFs give bibs, judge names and codes, per-judge marks, sums and results
(`Callback`, `Alt 1`, `-`). Prelim PDFs omit dancers with no Yes or Alternate
mark. A PDF prints names the tab masks.

Some names are masked at source (`******* *******`, with city `*******` and
country `**`): 220 of the 2026 finals' names. 2026 had 14 published WSDC
events with results, in Australia, New Zealand, Russia, Korea and the US.

## 3. URL patterns

```
GET https://danceconvention.net/eventdirector/robots.txt
GET https://danceconvention.net/eventdirector/en/eventsarchive                        2.9 MB, 1.66 MB gzip
GET https://danceconvention.net/eventdirector/en/eventpage/<eventId>/results          2.9 MB, 1.67 MB gzip
GET https://danceconvention.net/eventdirector/en/roundscores/<roundId>.pdf            65 to 115 KB (not fetched)
GET https://danceconvention.net/eventdirector/en/eventsarchive:loadyear?year=<yyyy>   XHR (not used)
GET https://danceconvention.net/eventdirector/en/upcoming                             (not used)
```

Event ids are 9 digits for recent events (`301273270`) and 7 digits for older
ones (`1601070` is WesterOz 2018). A slug after the id (`301367300-austin-...`)
is optional. Older ids in other links (for example `7495090-honey-fest`) can
be stale: the archive's id is the one to use.

## 4. Discovery

1. One `index` watch on the archive (`dcn.archive`), seeded by the adapter.
2. Rows come from `window.__NUXT__` →
   `state.common.currentPageRenderData.lastYearEvents[]`: `eventId`, `name`,
   `startDate`, `endDate` (`M/D/YY`), `affiliations`, `results`, `published`,
   `eventPage`. In October 2026 it lists the current year's 44 events.
3. A row is followed when it is published, its `affiliations` include
   `WSDC` and its `results` flag is true. DCN also hosts zouk, tango, ceroc and
   blues events; those rows are counted and dropped. A followed row becomes a
   listing (`source_event_row`, scope `source_index/dcn`) and one `event`
   watch on `.../eventpage/<eventId>/results` (`dcn.event`).
4. A WSDC event DCN does not label `WSDC` is not followed. Earlier years
   (`loadyear`) are not read; history comes from the Wayback Machine
   (section 10).

## 5. Change detection (verified)

- The HTML `ETag` changes on every response because a Sentry trace id
  is embedded in `<head>`. `Cache-Control: no-store`. CloudFront
  reports a miss every time. **Conditional GET never returns 304 here.**
- `Accept-Ranges: none`, so we cannot fetch only the tail where the
  payload lives.
- Both kinds use `change_mode = "extract"`: a response whose extract equals
  the watch's last one is not stored. The extract holds only the fields read
  (no event description, images or cities), so page decoration does not
  count as a change.
- PDFs have no validators and `no-store`.

## 6. Politeness settings

```toml
[hosts."danceconvention.net"]
min_gap_seconds = 2
daily_request_budget = 200
daily_byte_budget = "300MB"
index_interval_weekend = "6h"
index_interval_weekday = "24h"
live_interval = "30m"              # doubles after 8 unchanged, capped at 1 h
cooling_interval = "12h"           # doubles per unchanged check, capped at 24 h
```

Every poll is a full server render of a 2.9 MB page. That is why the
intervals and the budgets are stricter than for the other platforms, and
why a byte budget exists at all. The gap is the 2 s floor every host shares
([D-0247](../../../journal/decisions/0247-space-requests-two-seconds-apart-on-every-host.md)).
The scheduler caps an event watch's live interval at 1 h whatever this table
says ([scheduling](../scheduling.md)).

## 7. Fetch procedure

1. Archive: plain GET on the index schedule, decode the payload, emit the
   followed rows and their watches.
2. Results tab: plain GET on the event schedule (live, then cooling for 30
   days, then every 90 days). If `publishCompResults` is false, the page
   yields only its listing row and the warning `dcn_results_unpublished`.
3. A payload that fails to decode is an extract failure, not a fetch failure.
4. Score PDFs are not fetched (D-0255): they print names the tab masks, and
   no rule for those names exists yet.

## 8. Parsing

The payload is a data-serialization function call, not JSON. The pure decoder
(`dcn/nuxt.py`, [D-0068](../../../journal/decisions/0068-decode-dcn-source-data-without-running-scripts.md))
accepts only the reviewed literal-and-parameter grammar, without executing
JavaScript. See [the exact bounds](../parsing.md#parsing-rules). When a
capture repeats the render data under `data[0].renderData`, the two copies
must agree.

`dcn.archive` checks the route (`/<locale>/eventsarchive`), each row's id
against its `eventPage`, and the field types. `dcn.event` checks the route
(`/<locale>/eventpage/<id>/results`) against the event id, unique contest and
round ids, roles (`LEADER`, `FOLLOWER`), integer ranks and the score-sheet
path. Both decode HTML entities in text (`Swing &amp; Snow`). Any source field
the adapter does not read is listed in the extract's `unreviewed_fields`; the
admission contract then holds the page for review.

`dcn.event` emits:

- one listing row (scope `source_index/dcn:<id>`), with the dates written as
  `Sep 18 - 20, 2026` so the event projector reads a range;
- one `event_sheet` naming every round;
- one `round_sheet` per final whose ranks are exactly 1..N with no ties: a
  `Place | Leader | Follower` table. The competitor goes in its role's column
  and the partner in the other; a Pro-Am pro takes the partner column. A
  masked name is an empty cell, so no entry is created for it.

It withholds, with a bounded warning each:

- preliminary and semifinal rankings (`dcn_nonfinal_rounds_withheld`): they
  list only eliminated dancers and no marks, so the projector would publish a
  promoted count of 0;
- finals with no rankings (`dcn_empty_final_withheld`);
- finals that rank each role separately (`dcn_role_split_final_withheld`):
  four Pro-Am J&J finals in 2026; one table would repeat places.

`dcn_names_masked_at_source` counts the empty cells. City, country and
`proName` (the pro's name in Cyrillic) are never kept.

The admission contract is `admission/dcn_contract.py`, version 1. It accounts
for every extract field, requires one watch per followed archive row in source
order, requires the round sheets to be exactly the single-table finals with
at least one ranking, and holds a published tab with no contests for review.
No tab has removal authority: an empty or shrunken payload never retires
earlier accepted finals, and an unpublished tab has no terminal witness. Any
name containing `*` counts as masked, including partial masks. It
never imports the adapter; `tests/test_dcn_live.py` pins the rules the two
share.

## 9. Quirks

- `divisionType` values seen in 2026: `RANDOM_PARTNER`, `RANDOM_COUPLE`,
  `OPEN_COUPLE`, `PERM_COUPLE`, `PROAM_MIXED`, `PROAM_JNJ`, `SWITCH_JNJ`.
  Another value is an unreviewed field.
- Round names are free text (`Final `, `Prelims `, `Semi-final`, `Quarters`).
- A follower can be the competitor in a J&J final (Swingvasion), and switch
  finals mix roles row by row.
- Contest names often say `J&J` or `JnJ`, which the contest vocabulary does
  not yet read as Jack and Jill ([investigation](../../../journal/investigations/2026/dcn-results-source-2026-10-15.md#gaps-left)).
- Translations for every locale are inlined in the HTML, which is why
  the page is 2.9 MB.
- The heats WebView (`/eventdirector/en/hybrid/event/heats/<eventId>`)
  is client-rendered; whether it is public is **unverified**. Out of scope.
- `/eventdirector/rest/v2/...` and a WebSocket exist for registration.
  Not public, not used.

## 10. Backfill

The Wayback Machine has 1,188 distinct 200-status URLs under
`eventdirector/en/eventpage/` for 415 events, mostly 2019 to 2021, some
2025 and 2026. Enabling `dcn` turns on its CDX queries (prefix
`danceconvention.net/eventdirector/en/eventpage/*`), and `history/platform`
recognizes `.../eventpage/<id>/results` captures as `dcn.event` pages. Legacy
(pre-Nuxt) captures fail extraction; the offline `dcn.legacy_results` kind
below is not registered. Origin backfill is capped at one DCN event per day.

## 11. Load estimate

An event in its live window (36 h before its start to 48 h after its end)
is polled every 30 min, then hourly: about 130 polls and 380 MB over a
three-day weekend, about 70 MB a day. Cooling adds one poll a day for 30 days
(about 85 MB). The archive costs one to four polls a day (3 to 12 MB). DCN had
about 1.5 WSDC events a month in 2026, rarely two at once, so a typical day
is under 30 requests and 100 MB; two live events at once stay under the
300 MB byte budget. The first day after enabling costs 15 requests and
about 45 MB (the archive and the 14 tabs of 2026).

## 12. Operator switch

```
User-agent: swingset
Disallow: /
```

in `/eventdirector/robots.txt`, honored within 24 h. DCN already uses
per-agent groups (`spbot`, `Yandex`), so this is a pattern they know.

## 13. Open items

- Score PDFs: a parser for the 2026 layout and a rule for names the tab
  masks. Then prelims, semifinals, bibs and marks.
- Contest vocabulary for `J&J` and `JnJ`.
- **Find the JSON the Nuxt client uses for tab navigation.** A smaller data
  endpoint likely exists, but finding it means reading the site's JavaScript,
  which the [fetching rules](../fetching.md) forbid; ask the operator instead.
- Whether `ETag` would be stable if Sentry were disabled (ask the operator).
- Event ids the archive listing hides (7-digit ids) and earlier years.

## 14. Rollout

1. Deploy the package. Registering `dcn` edits `sources/registry.py`,
   `admission/contracts.py` and `state/recipe_scope.py`, which are in every
   recipe scope, so the deploy re-runs every extract, parse, project and link
   unit (as contract 5 did; maintenance item 20). Batch it with other
   all-scope changes.
2. Preflight receipt: `robots.txt` still allows the archive and results tabs;
   the host block in `config/hosts.toml` is as section 6.
3. Set `[sources.dcn] enabled = true` in `config/sources.toml` and switch.
   Expect 15 requests and about 45 MB on the first day.
4. Post-check receipt after one acquire tick: one `dcn.archive` snapshot, 14
   `dcn.event` watches, their snapshots parsed with no admission failures in
   shadow mode, 14 `source_events` rows for `dcn`, and the expected events
   mapped ([investigation](../../../journal/investigations/2026/dcn-results-source-2026-10-15.md#how-events-map-d-0253)).
5. Until a contract is active the kinds run in shadow: observations and
   projections exist, but the build keeps only rows equal to the baseline.
   Review the shadow generations with an independent check and activate
   contract 1 for `dcn.archive` and `dcn.event` with `swingset contract
activate` ([operation](../../guides/operation.md#activating-an-admission-contract)),
   as the 2026-10-06 adjudication did ([D-0240](../../../journal/decisions/0240-round-contract-5-blocks-remarks-that-contradict-the-shown-outcome.md)).
   Only then do the finals publish.

## Offline legacy kinds

`dcn/adapter.py` keeps four earlier offline kinds, all unregistered and
producing DCN-specific observations only: `dcn.list` (the 2025 archive shape),
`dcn.event_metadata` and `dcn.legacy_results` (the 2018 and 2019 Riga Summer
Swing Tapestry pages) and `dcn.score_pdf` (the two exact Riga score-PDF
layouts). Their fixtures and provenance are in `tests/fixtures/sources/dcn/`.
The receipts are the [offline parsing](../../../journal/investigations/2026/dcn-offline-parsing-2026-09-17.md),
[legacy results](../../../journal/investigations/2026/dcn-legacy-results-parsing-2026-09-17.md),
[origin acquisition](../../../journal/investigations/2026/dcn-origin-runner-2026-09-17.md),
[PDF controls](../../../journal/investigations/2026/dcn-score-pdf-controls-2026-09-17.md)
and [PDF parser](../../../journal/investigations/2026/dcn-score-pdf-parser-2026-09-17.md)
records. The Riga finals bib belongs to the row, not to either partner, and a
finals name differs between the 2019 HTML and the current PDF; neither is
resolved.
