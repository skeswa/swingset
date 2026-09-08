# Scraping techniques for swingset

Research artifact, written 2026-09-08. Facts about third-party sites
were checked that day with the requests recorded in
`verification/2026-09-08/` (response headers and the two scripts that
produced them; bodies were not kept because they contain names). Facts
we could not check are marked **unverified**. Library facts come from
official docs and READMEs fetched the same day; URLs are at the end.

## What this is for

The pipeline runs on hardware we own: a NixOS box in production and an
Apple Silicon Mac (M5 Pro, 48 GB) for development. No cloud scraping
service, no proxies, no third-party crawler. Several of the sites are run
by friends of the owner, and at least one (EEPro) has said an API is
coming. So the scraper is a stop-gap, and the first design goal is that
the operators never notice us in their logs except by name. Everything
below is ranked by how much origin load it removes.

## What we verified on 2026-09-08

One conditional-GET pair per host, plus a few single fetches. Gaps of at
least 5 s within a host. User-Agent `swingset/0.0 (+https://github.com/skeswa/swingset)`.

| Host | Server | Validators on results pages | Conditional GET | Bytes per poll (gzip) | Notes |
|---|---|---|---|---|---|
| `eepro.com` | Apache 2.4.62, Amazon Linux | strong `ETag` + `Last-Modified` on `.html` | **304 works** | 145 KB prelims page, 23 KB finals; 0 on 304 | `event.php` index has no validators (27 KB every time). `/results/<slug>/` is an Apache autoindex (1.8 KB) listing every file with its mtime. `/results/` root is an empty stub. `robots.txt` is 404. |
| `scoring.dance` | Cloudflare in front of Apache | `Last-Modified` only, and it is the **render time**, not a content time (two origin renders 7 s apart had different values) | 304 only when Cloudflare's edge still holds the page (`cf-cache-status: HIT`, `s-maxage=600`); on a MISS the origin re-renders and answers 200 | 8 KB event page; 335 KB sitemap | `cache-control: public, max-age=300, s-maxage=600, stale-while-ravlativate=86400000` (typo is theirs, so browsers ignore that directive). Brotli by default, gzip on request. `robots.txt` allows all and names the sitemap. Sitemap has 1,915 `enUS` URLs, 383 event ids (max 444), five routes per event, **no `lastmod`**. |
| `danceconvention.net` | Jetty 9.4 behind CloudFront | `ETag` present but **different on every response** (a Sentry trace id is in the `<head>`) | never 304 | **1.67 MB** gzip, 2.9 MB raw; the `__NUXT__` payload is only 124 KB of that | `Cache-Control: no-store`. `Accept-Ranges: none`, so we cannot fetch just the tail. PDFs have no validators and `no-store`; HEAD works. robots disallows registration flows, `eventpage:selectresultscontestrow`, and `eventpage.schedulecalendar:*`; results tab and `roundscores/*.pdf` are allowed. |
| `scores.worlddanceregistry.com` | S3 behind CloudFront (React Static v7) | weak `ETag` + `Last-Modified` | **304 works** (`x-cache: RefreshHit from cloudfront`, so CloudFront revalidates against S3 and S3 answers 304) | 40 KB gzip for the full rounds JSON (500 KB raw); 1.8 KB awards; 0 on 304 | `Cache-Control: no-cache, no-store, must-revalidate`. Every route has a `routeInfo.json` next to it with the page's data as plain JSON. Bucket root, `robots.txt`, and sitemap return 403. |
| `worldsdc.com` | Cloudflare, WordPress | none (`cf-cache-status: DYNAMIC`) | never 304 | 33 KB gzip (250 KB raw) | A GTranslate widget id changes every response, so a raw body hash always differs. `robots.txt` allows all; Yoast sitemap index exists. |

Wayback Machine coverage (CDX, `filter=statuscode:200`, `collapse=urlkey`):

| Prefix | Unique URLs with a 200 capture | Distinct events | Years |
|---|---|---|---|
| `eepro.com/results/*` | 1,124 | 141 slugs | 2016 to 2026 |
| `scoring.dance/enUS/events/*` | 3,155 | 330 event ids | 2021 to 2026 |
| `danceconvention.net/eventdirector/en/eventpage/*` | 1,188 | 415 events | 2017 to 2026, mostly 2019 to 2021 |
| `scores.worlddanceregistry.com/*` | 34 | 7 events | 2022 to 2026 |

## Techniques, ranked by origin load removed

### 1. Do not fetch: use the Internet Archive for anything old

Results pages older than 30 days do not change (WSDC rules require them
to stay up 30 days; corrections after that are rare). The Wayback
Machine already holds most of EEPro, scoring.dance, and DCN. Backfill
therefore reads the archive, not the origin:

- Query the CDX API once per source with `matchType=prefix`,
  `filter=statuscode:200`, `collapse=digest`, `output=json`, and the
  resume key for paging. The default cap is 150,000 rows per query.
- Fetch bodies with the `id_` flag
  (`https://web.archive.org/web/<timestamp>id_/<url>`) to get the
  original bytes without the toolbar or rewritten links.
- Treat the Wayback Machine as a host in `hosts.toml` with its own
  politeness settings. It has no published limit; community reports put
  CDX around 60 requests per minute and page fetches far lower before
  429s (**unverified**). Start at one request per 10 s and honor 429s.
- A capture counts only if it parses into results. Pages captured
  before results were posted, or holding fewer rounds than the event
  lists, are gaps; prefer captures made 30 days or more after the
  event. Fall back to the origin for gaps, newest first, at the lowest
  priority.
- Common Crawl is a second, weaker archive for hobby sites. Its index
  server asks not to be overloaded and rate-limits under load. Use it
  only for gaps, through `cdx_toolkit`, if at all.
- Save Page Now (SPN2) makes the Internet Archive fetch the origin. It
  is not a load-free option and is not used for polling.

### 2. Fetch the smallest thing that changes

Each source has a resource that is far smaller than the results page and
changes whenever the results do:

| Source | Small resource | Size | Replaces |
|---|---|---|---|
| EEPro | `/results/<slug>/` autoindex, which lists every file with mtime and size | 1.8 KB | polling each round page |
| World Dance Registry | `/<uuid>/rounds/routeInfo.json` (all rounds) and `/<uuid>/awards/routeInfo.json` | 40 KB gzip, 1.8 KB | rendering anything; and a 304 costs nothing |
| scoring.dance | none that is cheaper than the event results page itself (8 KB gzip); the sitemap is a discovery list, not a change signal | | |
| DCN | none. `Accept-Ranges: none`, `no-store`, per-response ETag. Every poll costs 1.67 MB. | | see the DCN guide for how we compensate |
| WSDC calendar | none. Daily fetch of 33 KB is fine. | | |

### 3. Conditional GET only where the validator is honest

- Honest: EEPro static files (strong ETag derived from size and mtime),
  S3 objects on World Dance Registry (weak ETag), Wayback bodies.
- Dishonest or absent: DCN (ETag changes per response), scoring.dance
  origin (`Last-Modified` is render time), WSDC calendar (none), EEPro
  `event.php` (none).
- Send `If-None-Match` and `If-Modified-Since` together when both are
  known; RFC 9110 says `If-None-Match` wins.
- Always send the same `Accept-Encoding: gzip`. Apache's `mod_deflate`
  appends `-gzip` to ETags (`DeflateAlterETag AddSuffix` is the 2.4
  default), so switching encodings between requests makes the validator
  look changed. Brotli is not requested because not every client we run
  on decodes it (this Mac's curl does not).

### 4. Fingerprint the content, not the bytes

Where validators are dishonest, decide "changed" from a normalized
fingerprint, as `fetching.md` already allows:

- DCN: hash the evaluated `__NUXT__` payload's `results` subtree.
- WSDC calendar: hash the events table after stripping the GTranslate
  ids (`gt-wrapper-<n>`, `gtranslateSettings['<n>']`).
- scoring.dance: the extract is the round and event links (event page)
  or the cells with their attributes (round page); whether the rest
  of the page carries nonces is **unverified**.
- EEPro and WDR: not needed, validators are honest.

In every case the fingerprint is the hash of the page kind's pure
`extract` output, the same value the parser consumes, not the visible
text: WSDC ids, callback flags, links, and
row classes live in attributes and can change on their own.

The design used to archive a body whenever its raw hash changed, which
for DCN would mean storing 1.67 MB per poll because of the nonce. The
plan changes that rule: when the fingerprint is unchanged the body is
discarded and only `checked_at` is recorded. We lose byte-identical
evidence of unchanged polls, which nobody needs.

### 5. Poll no faster than the CDN refreshes

- scoring.dance keeps a page at the edge for `s-maxage=600`. Any poll
  within 10 minutes of the last edge fill is answered by Cloudflare and
  never touches the origin. A poll after that triggers one origin
  render, exactly like one human visitor. The design's 15-minute live
  interval therefore costs the origin at most one render per poll.
- World Dance Registry says `no-store`, so CloudFront revalidates every
  request against S3; a 304 from S3 is a metadata read, which is the
  cheapest thing S3 does.
- DCN says `no-store` and CloudFront reports a miss each time; every
  poll is a full Jetty render. This is the one place our polling is
  expensive for the operator, so the DCN interval is longer (guide).

### 6. Give operators a switch they control

Protego reads per-agent groups. If a site adds

```
User-agent: swingset
Disallow: /
```

we stop within 24 hours (the robots refresh interval) with no code
change and no message needed. Each source guide tells the operator
this. We also keep `enabled = false` in `config/sources.toml` on our
side. RFC 9309 semantics we implement: 4xx on `robots.txt` (including
403, which the WDR bucket returns) means unrestricted; 5xx means
disallow all until the next successful fetch; cache for 24 h;
`Crawl-delay` is honored if larger than our gap even though it is not
part of the RFC.

### 7. Honor every standard slow-down signal

`Retry-After` on 429 and 503 (date or seconds, RFC 9110); the IETF
`RateLimit` and `RateLimit-Policy` fields (draft -11, May 2026, not
final; parse if present, never rely on); `Cache-Control: max-age` as a
floor on the re-poll interval; robots `Crawl-delay`. A 403 or a
Cloudflare challenge pauses the host for 24 h and is never retried with
different headers.

### 8. Extract embedded state instead of running a browser

All five sources put their data in the first response:

| Site | Where the data is | How we read it |
|---|---|---|
| EEPro | HTML tables | `selectolax` (lexbor backend) |
| scoring.dance | HTML tables with `data-wsdc`, `data-state` attributes | `selectolax` |
| DCN | `window.__NUXT__=(function(a,b,...){return {...}}(...))`, a Nuxt 2 function-call payload that dedups literals through parameters, so it is not JSON | evaluate in a sandboxed `node` subprocess (decision 18). `py-mini-racer` (V8 in a wheel, has timeouts) is the fallback if the subprocess is a problem on the NixOS box. The `quickjs` PyPI package's repo was archived on 2026-01-01 (**unverified**), so it is out. |
| World Dance Registry | `routeInfo.json` beside every route (React Static v7 writes it in `exportRoute.js`) and `window.__routeInfo` inline | `json.loads`; no JavaScript engine at all |
| WSDC calendar | HTML table | `selectolax` |

Playwright stays out of the pipeline. It fetches assets, executes
scripts, and costs the origin many requests per page for data that is
already in the HTML.

### 9. PDFs

`pdfplumber` (MIT, pure Python on `pdfminer.six`) for DCN round sheets
and the long tail. PyMuPDF is faster and has `find_tables()`, but it is
AGPL, which is a bad fit for a redistributable pipeline. Camelot needs
Ghostscript and tabula needs a JVM; both are awkward under nix plus uv.

### 10. Local LLMs only for the long tail, and only with review

For one-off PDFs, photographed score sheets, and blog posts, a local
model can produce a first draft of the records: Ollama structured
outputs (JSON-schema constrained since December 2024) or `llama.cpp`
with a GBNF grammar, and Qwen2.5-VL (Apache-2.0, 7B or 32B fits the dev
Mac) for images. The draft goes into the review queue and a person
signs off before it is merged. Deterministic parsers stay the only path
for the four platforms, because a model can misread a bib silently and
nobody would know.

### 11. Test without the network

`respx` mocks `httpx` routes; fixtures are real bodies from our archive
with expected records as JSON (golden files), as `parsing.md` says. The
fetch layer's politeness gate is tested with a fake clock.

## Things we considered and rejected

| Option | Why not |
|---|---|
| Scrapy | AutoThrottle raises concurrency toward a target; its delay is `latency / target_concurrency`, smoothed. It optimizes throughput, which is the opposite of a fixed 5 s floor and one in-flight request. Its cache and robots middleware are fine but bring a framework we do not need at under 2,000 requests a day. |
| crawlee-python | Autoscales on our CPU and memory, not on the server's comfort. |
| hishel (RFC 9111 cache for httpx; 1.3.1, August 2026) | Good library, but it would be a second store of validators next to the `watches` table, and the design wants conditional-GET behavior explicit and logged. Revisit if the hand-written layer grows past a few hundred lines. |
| aiolimiter, pyrate-limiter | A per-host "next allowed at" timestamp plus a lock is smaller than either dependency. |
| Headless browser by default | Multiplies origin requests; nothing here needs it. |
| `curl_cffi`, TLS impersonation, proxies, spoofed headers | Their purpose is to hide. We want to be found in the logs. |
| Cloudflare Web Bot Auth (RFC 9421 signatures), Verified Bots | Needs key hosting and a program application; built for large crawlers. Not worth it for a hobby project; the User-Agent link is our identification. |
| Save Page Now | Loads the origin from the archive's IPs. |
| HEAD requests | Same cost as a conditional GET on the servers that honor validators, and unreliable elsewhere. |
| `Range` on DCN | `Accept-Ranges: none`. |

## Load budget by source

Estimates for one event weekend under the guides' intervals. Live window
is about six days (36 h before start to 48 h after end).

| Source | Requests per event weekend | Bytes from origin | Origin work per poll |
|---|---|---|---|
| EEPro | about 150 autoindex polls, about 40 file fetches on listing change, and about 250 clocked conditional refreshes, almost all 304 | under 4 MB | static file read or stat |
| scoring.dance | about 150 event-page polls, about 30 first fetches of round pages, and about 1,000 clocked round refreshes (30 rounds, every 4 h live, then daily for 30 days) because the event page cannot reveal a corrected round | about 10 MB | one page render per poll when the edge has expired |
| World Dance Registry | about 150 polls, almost all 304 | under 1 MB | S3 metadata read |
| DCN | about 60 results-tab polls plus one PDF per round when it appears (about 20), fetched again once at day 30 | about 100 MB, dominated by the 1.67 MB page | full Nuxt render per poll |
| WSDC calendar | 1 per day | 33 KB | one WordPress render |
| WSDC registry | per design: 2 s gap, bootstrap once, then a trickle | tiny JSON | one database lookup |

DCN is the outlier. The guide halves its live poll rate relative to the
other sources and lists the JSON endpoint hunt as the first open item.

## Sources

- Scrapy AutoThrottle: https://docs.scrapy.org/en/latest/topics/autothrottle.html
- Scrapy downloader middleware (HttpCache, RobotsTxt): https://docs.scrapy.org/en/latest/topics/downloader-middleware.html
- crawlee-python scaling: https://crawlee.dev/python/docs/guides/scaling-crawlers
- hishel: https://hishel.com/ and https://pypi.org/project/hishel/
- Protego: https://github.com/scrapy/protego
- RFC 9309 robots.txt: https://www.rfc-editor.org/rfc/rfc9309.html
- RFC 9110 conditional requests and Retry-After: https://www.rfc-editor.org/rfc/rfc9110.html
- RateLimit header fields draft: https://datatracker.ietf.org/doc/draft-ietf-httpapi-ratelimit-headers/
- Cloudflare Web Bot Auth: https://blog.cloudflare.com/web-bot-auth/
- Cloudflare Content Signals Policy: https://blog.cloudflare.com/content-signals-policy/
- Cloudflare cache responses: https://developers.cloudflare.com/cache/concepts/cache-responses/
- CloudFront origin request behavior: https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/RequestAndResponseBehaviorCustomOrigin.html
- S3 ETags: https://docs.aws.amazon.com/AmazonS3/latest/userguide/ObjectETag.html
- Apache mod_deflate `DeflateAlterETag`: https://httpd.apache.org/docs/2.4/mod/mod_deflate.html
- Wayback CDX server: https://github.com/internetarchive/wayback/blob/master/wayback-cdx-server/README.md
- Wayback availability API: https://archive.org/help/wayback_api.php
- EDGI `wayback` client: https://github.com/edgi-govdata-archiving/wayback
- Common Crawl index: https://index.commoncrawl.org/ and https://github.com/cocrawler/cdx_toolkit
- Google on sitemap `lastmod`: https://developers.google.com/search/blog/2023/06/sitemaps-lastmod-ping
- selectolax: https://github.com/rushter/selectolax
- React Static `exportRoute.js`: https://github.com/react-static/react-static/blob/master/packages/react-static/src/static/exportRoute.js
- PyMiniRacer: https://github.com/bpcreech/PyMiniRacer
- quickjs (archived): https://github.com/PetterS/quickjs
- pdfplumber: https://github.com/jsvine/pdfplumber
- PyMuPDF license: https://pypi.org/project/PyMuPDF/
- Ollama structured outputs: https://ollama.com/blog/structured-outputs
- llama.cpp grammars: https://github.com/ggml-org/llama.cpp/blob/master/grammars/README.md
- Qwen2.5-VL: https://github.com/QwenLM/Qwen2.5-VL
- Playwright request interception: https://playwright.dev/python/docs/network
- respx: https://github.com/lundberg/respx
