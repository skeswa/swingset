# Fetch layer

## Identity

User-Agent:

```
swingset/<version> (+https://github.com/skeswa/swingset)
```

The linked README explains what we collect, why, how to opt out, and how
to ask us to slow down or stop. The contact channel is GitHub issues on
this repository, with issue templates for removal requests and for site
operators. There is no email address. A complaint that arrives by any
other path is treated as urgent because that person had no easy channel.

## Politeness rules

All values are per host unless stated. This table is the default for
any host. A playbook in `docs/sources/` may override a value for its
host in its section 6, and only there; no other document repeats
settings. `config/hosts.toml` holds exactly the defaults below plus the
playbook overrides, and `swingset doctor` prints the effective table so
review never depends on which document someone read. Overrides may
tighten a value or, for a host that is built for lookups (the
registry), loosen the gap with the reason recorded; the 5 s gap is the
floor everywhere else.

| Rule                                   | Value                                                                                                                                                                                                                                                                                          |
| -------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| In-flight requests per host            | 1                                                                                                                                                                                                                                                                                              |
| Minimum gap between requests to a host | 5 s (floor). 2 s for the registry sweep only, because responses are tiny and the site is built for this.                                                                                                                                                                                       |
| `Crawl-delay` in robots.txt            | Honored if larger than our gap.                                                                                                                                                                                                                                                                |
| Hosts fetched in parallel              | at most 4                                                                                                                                                                                                                                                                                      |
| Daily request budget per host          | 200 for any host without a playbook; playbooks set their own                                                                                                                                                                                                                                   |
| Daily byte budget per host             | none by default; a playbook may add one                                                                                                                                                                                                                                                        |
| Robots.txt                             | Fetched at most every 24 h. Parsed with Protego. A `User-agent: swingset` group wins over `*`, so any operator can stop or slow us without contacting us. 4xx (including 403, as on the WDR bucket) means unrestricted per RFC 9309. 5xx or unreachable means "disallow all" until next check. |
| Request timeout                        | 30 s connect + read                                                                                                                                                                                                                                                                            |
| Retries                                | 3, full-jitter exponential backoff starting at 10 s                                                                                                                                                                                                                                            |
| 429 or 503 with `Retry-After`          | Honor it exactly, minimum 60 s                                                                                                                                                                                                                                                                 |
| 429 or 503 without `Retry-After`       | Pause host for 15 min, doubling per repeat up to 24 h                                                                                                                                                                                                                                          |
| 403 or Cloudflare challenge            | Pause host 24 h. Log loudly. Never retry with different headers.                                                                                                                                                                                                                               |
| 404 on a watched URL                   | Mark watch `gone` after 3 consecutive 404s over 3 days; the registry's exact verified miss is lookup evidence instead (see its playbook)                                                                                                                                                       |
| Assets                                 | Never fetch images, CSS, JS, fonts                                                                                                                                                                                                                                                             |
| Compression                            | Send `Accept-Encoding: gzip`, always and only. Never brotli: Apache appends `-gzip` to ETags, so a changing encoding looks like a changed file, and not every client we run decodes brotli.                                                                                                    |
| Cookies                                | Not stored, not sent                                                                                                                                                                                                                                                                           |
| Per-source kill switch                 | `enabled = false` in config stops all fetches for that source                                                                                                                                                                                                                                  |

A run also has a wall-clock budget ([operations](operations.md)). When time runs out,
remaining due watches wait for the next run. Nothing is lost because
"due" is computed from state, not from a queue.

## Response classification

`classify(response_or_exception, page_kind, watch) -> Classification`
runs before host pause or failure state changes. Outcomes are `Ok`,
`NotModified`, `ExpectedUnavailable`, `Gone`, `Throttled(retry_after)`,
`Blocked`, `ServerError`, `Redirect`, and `Invalid`.

A page kind declares expected statuses in watch context. WDR's
`routeInfo.json` expects 403 until that watch has had a 200; afterward
403 is `Blocked`. EEPro and scoring.dance expect no such status.
Challenge detection (`cf-chl`, `Just a moment`, a Cloudflare challenge
body on any status) wins over expected statuses and is always `Blocked`.
`ExpectedUnavailable` and `Gone` affect only the watch. Automatic host
pause and failure state changes only on throttling, blocking, or server
errors, using the politeness table above. Operator pauses have their own
records and are not response classifications.

`Gate.acquire(host, now) -> Grant | Wait | Paused` checks in-flight
limits, gaps, budgets, robots, and pause state. Request accounting is
persisted when a request is issued; a crash never refunds a request
that may have reached the host. `Gate.release(host, classification,
now)` applies the classified host outcome. Redirect hops each acquire
the destination host's gate. Source adapters declare policy but do not
mutate host state.

## Change detection

Order of preference:

1. **Conditional GET.** Send `If-None-Match` with the stored ETag and
   `If-Modified-Since` with the stored `Last-Modified`. A 304 costs a few
   hundred bytes and records `checked_at` only.
2. **Body hash.** On 200, compute SHA-256 of the raw body. If it equals
   the last stored hash, treat as unchanged. Store nothing new except
   `checked_at`.
3. **Extract fingerprint.** Some pages embed nonces (DCN's Sentry
   trace id, the calendar's GTranslate id) or render-time validators
   (scoring.dance). The fingerprint is `sha256(canonical(extract(body)))`
   where `extract` is the page kind's pure content extractor from
   [parsing](parsing.md#contract). It covers exactly what the parser
   consumes, attributes included (WSDC ids, callback flags, links, row
   classes), so hashing is never written separately from parsing. If
   the fingerprint is unchanged the body is **discarded** and only
   `checked_at` is recorded; if `extract` fails the body is archived
   and flagged; storing 1.67 MB of DCN per poll for a nonce is not worth
   it. Which sources need this is in each playbook.
4. **HEAD** is not used. A conditional GET is cheaper than HEAD plus GET
   and the same size as a HEAD when unchanged.

The registry's POST endpoint supports none of this. Each refresh is a
full fetch of a few KB. The schedule (5.2) keeps those rare.

## Archive

- Blob store: `blobs/sha256/<aa>/<bb>/<hash>` holding the raw body,
  gzip-compressed if not already compressed. Identical bodies are stored
  once.
- `snapshots` table: `snapshot_id`, `watch_id`, `method`, `url`, `form`, `fetched_at`,
  `http_status`, `etag`, `last_modified`, `content_type`, `body_sha256`,
  `body_bytes`, `content_changed`, `run_id`. Response headers stored as
  JSON. Request form data uses canonical JSON and is nullable for GET.
  Classification, parse and extract versions, statuses, and derived
  artifact references are listed in [local state](state.md#sqlite-schema).
- We keep every changed snapshot forever. Storage is small: a few MB per
  event weekend after compression and dedup.
- Derived blobs: an evaluated DCN payload is stored once as JSON next
  to its raw body in `extracts/` so parser-only changes do not re-run
  `node`. An extractor upgrade deliberately re-evaluates it.
- The Wayback Machine is a transport in this layer. A backfill watch
  carries an archive URL (`web.archive.org/web/<ts>id_/<url>`); the
  body is archived under the original URL with `via = wayback`,
  `captured_at` from the `memento-datetime` header, and the origin's
  `x-archive-orig-*` headers as validators. `web.archive.org` is a host
  in `hosts.toml` with its own gate (10 s, 200 requests a day to start,
  120 s CDX timeout). Capture selection, CDX indexing, the `sealed`
  state, and origin fallback are owned by [backfill](backfill.md).
  Conflict resolution and observation ownership compare `observed_at`
  (capture time for archive bodies, fetch time otherwise), never our
  fetch time alone.
- WARC was considered. A plain content-addressed store plus SQLite is
  simpler and deduplicates better. A WARC export command can be added
  later without changing anything else.
