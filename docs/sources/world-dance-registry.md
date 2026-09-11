# World Dance Registry "Pro Score" (`scores.worlddanceregistry.com`)

## 1. Status

Verified 2026-09-08 (`research/verification/2026-09-08/wdr_*.hdr`).
Discovered during the 2026-09 research; not in the original design. The
operator relationship is unknown. `robots.txt`, the bucket root, and
`sitemap.xml` return 403 from S3. Under RFC 9309 a 4xx on `robots.txt`
means unrestricted, but 403 on the root also means there is no index we
can read. No terms page found. The registration side
(`www.worlddanceregistry.com/event.aspx?euid=<uuid>`) is a separate
ASP.NET site we do not fetch.

## 2. What it gives

Every round of every contest as JSON: bibs, names, judges (first names
only), per-judge marks, sums, callbacks, finals ranks, tallies. No WSDC
ids. Non-called-back competitors in prelims are redacted: name `***`,
marks `0.00`, bib kept. 14 events in the last year, US and Canada
(Trilogy, Swing City Chicago, Chicago Classic, Montreal Westie Fest,
Carolina Summer Swing, Florida Dance Magic, Desert City Swing, and more),
and growing.

## 3. URL patterns

```
GET https://scores.worlddanceregistry.com/<uuid>/                     HTML shell + inline window.__routeInfo
GET https://scores.worlddanceregistry.com/<uuid>/routeInfo.json       138 B: {"data":{"eventName":...}}
GET https://scores.worlddanceregistry.com/<uuid>/awards               HTML
GET https://scores.worlddanceregistry.com/<uuid>/awards/routeInfo.json 1.8 KB: finals names and places
GET https://scores.worlddanceregistry.com/<uuid>/rounds               HTML
GET https://scores.worlddanceregistry.com/<uuid>/rounds/routeInfo.json 500 KB raw, 40 KB gzip: every round
```

`<uuid>` is a v1 UUID (`98011277-01cd-11f1-9a29-0aa72bbce9ea`), not
guessable. The registration `euid` is a different UUID format; whether
the two are related is **unverified**.

React Static v7 writes `routeInfo.json` beside every exported route and
inlines the same object as `window.__routeInfo` in the HTML; the JSON
is the one we use, so no JavaScript engine is involved.

## 4. Discovery

There is no index. Three feeders, in order:

1. `overrides/source_urls.csv` rows with `source = worlddanceregistry`
   (the research CSV already has 14).
2. The event-site link scan: the event's own website (from the WSDC
   calendar) is fetched daily from 14 days before the event through 30
   days after it, until a link to `scores.worlddanceregistry.com/<uuid>`
   or another results platform is found. WDR links usually appear
   during or after the event, so the scan cannot stop at the start.
   This is the only automatic path.
3. The Wayback CDX for `scores.worlddanceregistry.com/*` (34 URLs, 7
   events on 2026-09-08), read once a year for backfill.

An event whose calendar site has no results link and no override is
simply not covered until someone adds the UUID. The research showed
this happens (Desert City Swing 2026, Cash Bash 2025).

## 5. Change detection (verified)

- `routeInfo.json` carries a weak `ETag` and `Last-Modified`.
  `If-None-Match` returns 304 (`x-cache: RefreshHit from cloudfront`:
  CloudFront revalidates against S3 because the objects say
  `no-store`, and S3 answers 304 from metadata).
- The whole site is rebuilt when the operator publishes;
  `siteData.lastBuilt` in the HTML's `__routeInfo` and every object's
  `Last-Modified` move together. No fingerprint needed.

## 6. Politeness settings

```toml
[hosts."scores.worlddanceregistry.com"]
min_gap_seconds = 5
daily_request_budget = 400
live_interval = "15m"              # doubles after 8 unchanged, max 1h
cooling_interval = "6h"
```

## 7. Fetch procedure

1. `event` watch on `/<uuid>/rounds/routeInfo.json`: conditional GET.
   On 304 record `checked_at`. On 200 archive and parse.
2. `awards` watch on `/<uuid>/awards/routeInfo.json`: same. Fetched in
   the same cycle as the rounds file, since they change together.
3. The HTML routes are never fetched; `routeInfo.json` has everything.
4. A 403 on a `routeInfo.json` means the event is not published yet or
   the UUID is wrong; treat as `upcoming` and retry daily, up to 30
   days, then mark `gone`.

## 8. Parsing

`wdr.rounds` on `rounds/routeInfo.json`:

- `data.scoresData.eventName`, `data.scoresData.results[]` =
  `{id, roundName, roundSubHeader, redacted, attributeGroup, results[]}`.
- `roundName` is `"<Contest> - <Division> - <Round>"`, e.g.
  `Jack & Jill - Advanced - Prelim Round 1`, `Strictly Swing - Novice - Final`.
  Split on `-`.
- `roundSubHeader` is the legend: `Placement Order` for finals,
  `Sum of Yes(10) / Alt 1(4.5) 2(4.3) 3(4.2) / No(0)` for prelims.
- `results[]` holds one table for finals and couples contests, two for
  Jack and Jill prelims (leaders, followers). Each table is
  `{data: rows[]}`, each row a list of cells `{t, v, s}`. Row 0 is the
  header.
- Cell type codes `t` (from the header row; **partly inferred**):
  `2` Callback (`Y`, or `S<n>` for not called back, meaning of `S`
  **unverified**), `3` `#` (row ordinal), `4` `Bib #`, `5` name
  (`Leaders`, `Followers`, `Leader`, `Follower`), `6` `Final` place,
  `7` `Scores Sum`, `9` judge column (header is the judge's first name;
  value is a mark `10.00`/`4.50`/`4.30`/`4.20`/`0.00` in prelims or a
  rank in finals), `12` tally column (`1--1`, `1--2`, ...; values like
  `2 (2)`; `s = true` shades the majority cell).
- Canonical callbacks use only explicit `Y` as `promoted`. An `S<n>` value stays
  in the raw observation and opens one finding for its round; no callback outcome
  or promoted count is emitted because the meaning remains unverified. Likewise,
  an unrecognized numeric judge value stays raw and is never converted to `no`.
- Finals show one `Bib #` per couple row (**which partner's bib is
  unverified**), leader and follower names, and place. The canonical partner
  entries therefore leave `bib` null while the raw table retains the value.
- Masked rows use the name `***` and placeholder marks. The sample includes
  15 such rows even where the table says `redacted = false`; detect the row
  marker directly. Retain bib-based entries with `name_raw = null`, and emit
  no marks or callback outcome for those rows. One bibless masked row is
  omitted with a finding because its identity is not recoverable. Named rows
  in the same table retain their real marks.
- Explicit `Am`/`Pro` columns use the contest's amateur Leaders/Followers
  label to assign complementary roles. Without that label, role-specific
  output is withheld with a finding. A single `Couple` cell stays a couple
  entry; its combined name does not establish each person's role.
- Judges are first names only; `judges.anonymous = false` but
  `name_raw` is the first name. Judge linking will be weak here.

`wdr.awards` on `awards/routeInfo.json`: `data.awardsData.results[]` =
`{id, roundName, data: [[Leader, Follower, Final], [name, name, place], ...]}`.
Used to cross-check finals places from the rounds file.

## 9. Quirks

- Brotli by default; gzip when requested.
- `Cache-Control: no-cache, no-store, must-revalidate` on every object,
  so nothing is cached at the edge; our 304s cost S3 a metadata read.
- The `rounds` JSON includes every division at once; there is no
  per-round URL.
- `attributeGroup` is usually null. Three routine finals in the 13-event
  sample use a group named `NASDE`. Its own labels define `SVW` as a swing
  violation warning and `SV1`, `SV2`, and `SV3` as 1-, 3-, and 10-placement
  drops. Preserve the group and labels; do not apply the penalty a second time.

## 10. Backfill

Only 7 events are in the Wayback Machine. Historical UUIDs come from
event sites' old pages (which the archive does hold) and from
overrides. Each is a 40 KB fetch, so origin backfill is cheap once the
UUID is known.

## 11. Load estimate

Per event weekend: about 150 polls, almost all 304s, and a handful of
40 KB downloads. Under 1 MB.

## 12. Operator switch

The site cannot serve a `robots.txt` today (403). The switch is a
GitHub issue or an email to the owner, or a CloudFront rule on our
User-Agent; a 403 on a known-good URL pauses the host for 24 h and is
reported.

## 13. Open items

- Meaning of `S<n>` in the callback column.
- Whether the finals `Bib #` is the leader's bib.
- Whether registration `euid` values map to scores UUIDs.
- Whether the operator would publish an index (one JSON file listing
  event UUIDs would remove the discovery problem entirely).

## 14. Implementation fixture check (2026-09-09 UTC)

The gated collector archived rounds and awards for UUID
`98011277-01cd-11f1-9a29-0aa72bbce9ea`: 32 round observations and
13 award observations. A repeated conditional rounds request returned 304.
Fixtures and request metadata are in `src/swingset/sources/wdr/fixtures/`.

The committed Desert City sample does not establish the meaning of `S<n>` or
which partner owns the finals bib. Those remain unverified and produce parser
warnings where encountered. The broader sample establishes the NASDE
`attributeGroup` described above; other groups remain unverified. A single
conditional request does not establish a weekend's load.

The seed script initially found 12 usable UUID URLs among 14 research mentions.
The official Swingapalooza 2026 results page now supplies UUID
`00cf1615-fb0d-11f0-9a29-0aa72bbce9ea`, and its rounds payload names the event
`Swingapalooza 2026`. The official Jax results page links UUID
`ed59db24-679d-11ef-9a29-0aa72bbce9ea`, but that payload names the event
`Jax Westie Fest 2025`; the exact Jax Westie Fest 2026 UUID is still required.

All 12 initial exact seed URLs and the verified Swingapalooza URL were fetched
through the gated collector on 2026-09-09.
Every rounds and awards URL returned valid JSON and parsed successfully: 436
round observations and 259 award observations for the initial 12, plus 34
rounds and 24 awards for Swingapalooza, for totals of 470 and 283. Per-event
round counts ranged from 18 (Waterloo) to 52 (Chicago Classic); award counts
ranged from 10 to 34. The captures are archived in `tmp/wdr-smoke`; the committed Desert City
fixtures remain the representative parser fixture. Swingapalooza and the stale
Jax 2025 link are archived separately in `tmp/wdr-discovery-results`.

The broader sample contains `S<n>` callback values in every event; their meaning
remains unverified. Non-null `attributeGroup` values in two events establish the
NASDE routine-penalty legend described above. Finals bib ownership also remains
unverified. No UUID is inferred from the separate World Dance Registry
registration `euid`.
