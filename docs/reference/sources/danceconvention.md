# danceconvention.net (DCN)

danceconvention.net provides event results and linked score files. Some adapter and fixture work remains; a documented page format is not proof of deployed support.

[All sources](README.md) · [Shared fetching rules](../fetching.md)

## 1. Status

Verified 2026-09-08 (`journal/evidence/collection/source-survey-2026-09-08/dcn_*.hdr`).
The operator relationship is not known to be personal; treat as a
stranger and be stricter, not looser. `robots.txt` (at
`/eventdirector/robots.txt` after a redirect from `/robots.txt`)
disallows registration flows, `index:retrievenewsfeed`,
`eventpage:selectresultscontestrow`, `eventpage:selectsignupscontestrow`,
`eventpage:invitepartner`, `systemsignup.systemsignupform`, and
`eventpage.schedulecalendar:*` in every locale. The results tab and
`roundscores/*.pdf` are allowed. No terms page found (2026-09-04).

## 2. What it gives

Per contest and round: names with city and country, rank, whether
promoted (from ranking intervals), and per-round PDFs with bibs, judge
names, and marks. **No bibs or WSDC ids outside the PDFs.** Some names
are masked at source (`*******`). 19 events in the last year:
Australia, New Zealand, Korea, Singapore, Russia.

## 3. URL patterns

```
GET https://danceconvention.net/eventdirector/robots.txt
GET https://danceconvention.net/eventdirector/en/upcoming
GET https://danceconvention.net/eventdirector/en/eventsarchive
GET https://danceconvention.net/eventdirector/en/eventsarchive:loadyear?year=<yyyy>   XHR: send X-Requested-With: XMLHttpRequest
GET https://danceconvention.net/eventdirector/en/eventpage/<eventId>[-slug]/results  2.9 MB raw, 1.67 MB gzip
GET https://danceconvention.net/eventdirector/en/roundscores/<roundId>.pdf
```

Event ids are 9 digits for recent events (`301273270`) and 7 digits for
older ones (`1601070` is WesterOz 2018). The archive listing does not
show the old ones.

## 4. Discovery

1. `upcoming` and `eventsarchive` on the index schedule. Both are Nuxt
   pages of the same size as a results page, so the index schedule for
   this host is slower than elsewhere (section 6).
2. Rows come from `window.__NUXT__` →
   `state.common.currentPageRenderData.upcomingEvents|lastYearEvents[]`
   with `eventId`, `name`, dates, `location`, `affiliations`
   (`["WSDC"]`), `results`, `published`, `eventPage`.
3. Only events with `WSDC` in `affiliations`, or matched by name to the
   calendar, get a results watch. DCN hosts zouk, tango, and other
   events we do not want.
4. Older years: `eventsarchive:loadyear?year=<yyyy>` as an XHR, once
   per year of history, for backfill only.

## 5. Change detection (verified)

- The HTML `ETag` changes on every response because a Sentry trace id
  is embedded in `<head>`. `Cache-Control: no-store`. CloudFront
  reports a miss every time. **Conditional GET never returns 304 here.**
- `Accept-Ranges: none`, so we cannot fetch only the tail where the
  payload lives (the `__NUXT__` script is the last 124 KB of 2.9 MB).
- Change is decided by fingerprint: SHA-256 of the canonical JSON of
  `currentPageRenderData.results` after evaluating the payload. When
  unchanged, the body is discarded, not archived.
- PDFs have no validators and `no-store`. A PDF is fetched once when
  its round first shows `scoresAvailable = true`, and again only when
  the round's rankings in the payload change.

## 6. Politeness settings

```toml
[hosts."danceconvention.net"]
min_gap_seconds = 10
daily_request_budget = 200
daily_byte_budget = "300MB"
index_interval_weekend = "6h"
index_interval_weekday = "24h"
live_interval = "30m"              # doubles after 4 unchanged, max 2h
cooling_interval = "12h"           # doubles per unchanged check, max 48h
```

Every poll is a full server render of a 2.9 MB page. That is why the
gap, the intervals, and the budgets are all stricter than for the other
platforms, and why a byte budget exists at all.

## 7. Fetch procedure

1. `index` watches: plain GET, evaluate the payload, fingerprint the
   event list, upsert `source_events`, create `event` watches.
2. `event` watch on `.../results`: plain GET. Evaluate the payload in
   the `node` subprocess (5 s timeout, no network, stdin only). If
   `publishCompResults` is false, treat as unchanged. Fingerprint
   `results`. If changed, archive the body and parse. For each round:
   create a `pdf` watch and make it due when `scoresAvailable` turns
   true for the first time, whether or not the rankings moved; make an
   existing `pdf` watch due again when the round's rankings changed.
   Rankings often appear before the PDF does, so availability alone is
   a trigger.
3. `pdf` watch: plain GET, archive, parse with `pdfplumber`. Because
   a PDF can be corrected without the payload's rankings moving, every
   `pdf` watch is fetched once more at the end of the cooling window
   (30 days after the event) as a final sweep. No other clocked PDF
   refresh: at 1.67 MB per parent poll and no validators on PDFs, the
   sweep is the cheapest correction check this host allows.
4. A payload that fails to evaluate is a parse failure, not a fetch
   failure; the body is kept and the run continues.

## 8. Parsing

- `dcn.event_results`: payload path
  `currentPageRenderData.results[]` =
  `{contestId, contestName, divisionType: RANDOM_PARTNER|OPEN_COUPLE|PERM_COUPLE, rounds[]: {roundId, roundName, isFinal, scoresAvailable, scoresLink, rankings[]: {competitorRole, competitorName, competitorCityAndState, competitorCountry, partnerName, partnerCityAndState, partnerCountry, rank, interval}}}`.
  `roundName` is free text with typos ("Prelilms"). Emits `contest`,
  `round`, `ranking`, and `pdf` watches.
- `dcn.round_pdf`: finals `# | Name (leader, newline, follower) | per-judge ranks | tally | Result`;
  prelims `# | Name | per-judge 10/4.5/4.3/4.2/0 | Sum | Result`; judges
  listed above the table; older PDFs use the 1/2.x/3 legend and may be
  localized. Locate tables by the `Result` header. Bibs are only here.
- `dcn.list`: `upcomingEvents` and `lastYearEvents` from the payload;
  the `loadyear` XHR returns Tapestry HTML with the same fields in a
  table.
- The payload is a function call, not JSON. It is evaluated by
  `sources/dcn/nuxt_eval.js` under `node` (decision 18). The evaluated
  object is cached in the archive as a derived blob so re-parses do not
  re-evaluate.

## 9. Quirks

- Names masked as `*******` at source (a Korea Westival 2026 finalist).
  Keep the mask in `name_raw`; the entry gets `link_status = unlinkable`.
- `publishCompResults` can be false while rounds exist; nothing is
  public then.
- Translations for every locale are inlined in the HTML, which is why
  the page is 2.9 MB.
- The heats WebView (`/eventdirector/en/hybrid/event/heats/<eventId>`)
  is client-rendered; whether it is public is **unverified**. Out of
  scope.
- `/eventdirector/rest/v2/...` and a WebSocket exist for registration.
  Not public, not used.

## 10. Backfill

The Wayback Machine has 1,188 distinct 200-status URLs under
`eventdirector/en/eventpage/` for 415 events, mostly 2019 to 2021, some
2025 and 2026. Because a live DCN fetch is 1.67 MB, the archive is used
for every event whose capture has `publishCompResults` true and PDFs
for every round, and origin backfill for the rest is capped at one DCN
event per day.

## 11. Load estimate

Per event weekend: about 60 results-tab polls at 1.67 MB, plus about 20
PDFs at under 100 KB each, fetched once when they appear and once more
in the day-30 sweep. About 100 MB from the origin and about 80
requests over six days. Twenty times the bytes of any other platform
per event; the count of DCN events (19 a year) keeps the total tolerable.

## 12. Operator switch

```
User-agent: swingset
Disallow: /
```

in `/eventdirector/robots.txt`, honored within 24 h. DCN already uses
per-agent groups (`spbot`, `Yandex`), so this is a pattern they know.

## 13. Open items

- **Find the JSON the Nuxt client uses for tab navigation.** Switching
  tabs in the browser does not reload the 2.9 MB page, so a smaller
  data endpoint exists. Read the four `/eventdirector/_nuxt/*.js`
  bundles once (about 1 MB, one-time) and look for the request that
  fills `currentPageRenderData`. If it is unauthenticated and allowed
  by robots, it replaces step 2 above and cuts DCN load by roughly 50×.
- Whether `ETag` would be stable if Sentry were disabled (ask the
  operator; a `Vary`-free stable ETag would make conditional GET work).
- Whether the PDFs' bibs match the app's bib numbers.
- Event ids the archive listing hides (7-digit ids).
