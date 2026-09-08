# scoring.dance

## 1. Status

Verified 2026-09-08 (`research/verification/2026-09-08/sd_*.hdr`).
Operator relationship: for the owner to fill in before phase 3. A paid API exists (RapidAPI and
`pointstest2.scoring.dance`, **unverified**); if it covers results at a
fair price it replaces this playbook (open question 1 in the design).
`robots.txt` allows everything and names the sitemap. No terms page was
found (checked 2026-09-04).

## 2. What it gives

Everything EEPro gives, plus **WSDC ids** on every named competitor.
This is the only platform that prints them, which makes it the anchor
for identity linking. 87 of the last year's events, mostly Europe, 11
in the US and a few in Canada and Asia.

## 3. URL patterns

```
GET https://scoring.dance/robots.txt
GET https://scoring.dance/sitemap.xml                          all events, 5 routes each, no lastmod (335 KB)
GET https://scoring.dance/enUS/recent                          past events only (noscript list)
GET https://scoring.dance/enUS/events/<eventId>/results/       event results page (8 KB gzip)
GET https://scoring.dance/enUS/events/<eventId>/results/<roundId>.html
```

Never fetch `/enUS/wsdc/...` paths: Cloudflare served a JS challenge
there on 2026-09-04. Never fetch `register`, `wall`, `mockjudge`, or
`news` routes; they are in the sitemap but not for us.

## 4. Discovery

1. Fetch `sitemap.xml` on the index schedule. Extract every
   `/events/<id>/` id (383 ids, highest 444 on 2026-09-08). New ids
   become `source_events` with no name yet.
2. Fetch `/enUS/recent` on the same schedule for names and dates of
   past events. Upcoming events' names come from the event page itself
   on its first `upcoming` fetch.
3. Match to `events` by name and date overlap. City of Angels 2026
   (id 315) is in the sitemap but was missing from `recent` on
   2026-09-04, so the sitemap is the source of truth for ids.

## 5. Change detection (verified)

- The HTML carries `Last-Modified` but it is the render time: two
  origin renders 7 s apart returned different values. It is not a
  content validator. There is no `ETag` on HTML.
- Cloudflare caches pages for `s-maxage=600` (10 min) at the edge and
  tells browsers `max-age=300`. While the edge copy is fresh
  (`cf-cache-status: HIT`, `age` under 600), a request with
  `If-Modified-Since` set to the stored `Last-Modified` gets a 304 from
  the edge and the origin is not touched. After that the origin renders
  once and answers 200 with a new `Last-Modified`.
- So: send `If-Modified-Since` anyway (free 304s while the edge is
  warm), and decide "changed" by fingerprint. The fingerprint is the
  hash of the page kind's `extract` output (`design/parsing.md`), so it
  covers every field the parser consumes, attributes included:
  - event page: the ordered list of contests and rounds with their
    `<roundId>.html` links. Event 418's results page has 12 round links
    and no tables, so a table-based fingerprint would never change.
  - round page: every cell of `table.table` plus `data-wsdc`,
    `data-state`, judge `title` attributes, and the chief-judge marker.
    A corrected WSDC id or callback flag changes the fingerprint even
    when the visible text does not.
  The rest of the page is assumed nonce-free (**unverified**; if raw
  hashes differ while the fingerprint does not, that assumption
  holds).
- The `cache-control` header contains a misspelled directive
  (`stale-while-ravlativate`). Do not rely on stale-while-revalidate
  behavior.

## 6. Politeness settings

```toml
[hosts."scoring.dance"]
min_gap_seconds = 5
daily_request_budget = 800
index_interval_weekend = "1h"      # sitemap and recent
index_interval_weekday = "6h"
live_interval = "15m"              # event page; doubles after 8 unchanged, max 1h
cooling_interval = "6h"
round_live_interval = "4h"         # each round page, while the event is live
round_cooling_interval = "24h"     # each round page, for 30 days after
challenge_pause = "24h"            # any Cloudflare challenge page
```

Round pages need their own clock. A corrected WSDC id, callback flag,
or mark inside a round changes nothing on the event page, whose link
list is the only parent signal, so the parent cannot be trusted to
announce corrections. WSDC rules allow corrections for 30 days.

A live poll every 15 min means the edge copy has expired (600 s) by the
time we return, so each poll is one origin render. That is the same
cost as one visitor reloading the page four times an hour.

## 7. Fetch procedure

1. `index` watches: `sitemap.xml` and `/enUS/recent`, plain GET with
   `If-Modified-Since` (both send `Last-Modified`). Fingerprint the
   sitemap by its set of `<loc>` values and `recent` by its parsed
   rows.
2. `event` watch on `/enUS/events/<id>/results/`: GET with
   `If-Modified-Since`. On 200, fingerprint the contest and round
   links. If unchanged, discard the body and record `checked_at`. If
   changed, archive it, parse, create watches for new rounds, and mark
   every round watch of the event as due now (the event page does not
   say which round changed).
3. `round` watch on `/results/<roundId>.html`: fetched when the event
   page's fingerprint changed, when the round is new, and on its own
   clock: every 4 h while the event is live, every 24 h while cooling,
   then on the 90-day archived schedule. Same conditional and
   fingerprint rules; a round whose fingerprint changed resets the
   event's live interval to 15 min.
4. Any response whose body contains a Cloudflare challenge marker
   (`cf-chl`, `Just a moment`) pauses the host for 24 h and fails the
   run so a person looks.

## 8. Parsing

From `design/parsing.md` and `design/sources.md`:

- `scoringdance.event`: the results page lists contests and rounds with
  links to `<roundId>.html`. Emits `contest`, `round`, and round
  watches.
- `scoringdance.round`: `table.table.w-100`. Judges in
  `<th title="Full Name">KS</th>` (full name in `title`); the chief
  judge carries an icon. Rows have `data-state="CB"` when called back.
  Name cells link to `/enUS/wsdc/registry/<id>.html` and carry
  `data-wsdc="<id>"`. Prelims: bib, name, per-judge Yes/Alt1/No, sum.
  Finals: bib, leader, follower, per-judge rank, placement.
- `scoringdance.recent`: noscript list of past events, id from the link.
- `scoringdance.sitemap`: `<loc>` values matching `/events/(\d+)/`.

## 9. Quirks

- Brotli is served unless gzip is requested; we request gzip.
- Sitemap locales: every event has a locale-free `/events/<id>/...`
  entry and `enUS` entries with `hreflang` alternates. Use only the id.
- `recent` omits some events (City of Angels 2026).
- Some events have no callbacks reported by the research agents;
  whether that is a page difference or a research gap is
  **unverified**.

## 10. Backfill

The Wayback Machine has 3,155 distinct 200-status URLs under
`scoring.dance/enUS/events/` for 330 event ids, 2021 to 2026. Round
pages that the archive holds are read from there, taking the latest
capture that parses. Event ids missing from the archive, or whose
captures hold fewer rounds than the event page lists, are fetched from
the origin, newest first, lowest priority, at most one event per cycle.

## 11. Load estimate

Per event weekend: about 150 event-page polls (8 KB gzip, 304 while the
edge is warm), about 30 first fetches of round pages, and clocked round
refreshes: 30 rounds at 6 a day for the six live days is about 1,000
requests, then 30 a day for 30 days. About 1,200 requests and 10 MB per
event over the live window, so three European events on one weekend
approach the 800-a-day budget; when the budget is hit, round refreshes
are what wait, never event-page polls. The sitemap (335 KB) is fetched at most 4
times a day off-season, 24 on event weekends; a 304 on it is expected
most of the time since its `Last-Modified` was a day old when checked.

## 12. Operator switch

```
User-agent: swingset
Disallow: /
```

in `robots.txt`; honored within 24 h. Or a Cloudflare rule on our
User-Agent; a challenge or 403 pauses us for 24 h and is reported to
the owner as an incident, never retried with other headers.

## 13. Open items

- Whether the paid API covers round marks and WSDC ids, and its price.
- Whether round pages show heats.
- Stability of Cloudflare's behavior on `/enUS/events/` at 15-minute
  polling. Measured over the first month from our own snapshots.
- Whether any part of the page other than the tables changes between
  renders (nonce check).
