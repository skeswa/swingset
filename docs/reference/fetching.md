# Fetch layer

This page owns the rules for requesting and saving source pages. A host is the website serving a request. Shared limits live here; verified exceptions belong in the [source guides](sources/README.md).

[Reference index](README.md)

## On this page

- [Identity](#identity)
- [Politeness rules](#politeness-rules)
- [Guards](#guards)
- [Response classification](#response-classification)
- [Change detection](#change-detection)
- [Archive](#archive)
- [Registry check evidence (H1)](#registry-check-evidence-h1)

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
any host. A playbook in `docs/reference/sources/` may override a value for its
host in its section 6, and only there; no other document repeats
settings. `config/hosts.toml` holds exactly the defaults below plus the
playbook overrides, and `swingset doctor` prints the effective table so
review never depends on which document someone read. Overrides may
tighten a value. No override may set a gap below the 2 s floor
([D-0247](../../journal/decisions/0247-space-requests-two-seconds-apart-on-every-host.md)).

| Rule                                   | Value                                                                                                                                                                                                                                                                                        |
| -------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| In-flight requests per host            | 1                                                                                                                                                                                                                                                                                            |
| Minimum gap between requests to a host | 2 s (floor), on every host. The Internet Archive keeps its own 10 s.                                                                                                                                                                                                                         |
| `Crawl-delay` in robots.txt            | Honored if larger than our gap: the gap is the largest of the host's gap, `Crawl-delay` and 2 s.                                                                                                                                                                                             |
| Hosts fetched in parallel              | at most 4                                                                                                                                                                                                                                                                                    |
| Daily request budget per host          | 200 for any host without a playbook; playbooks set their own                                                                                                                                                                                                                                 |
| Daily byte budget per host             | none by default; a playbook may add one                                                                                                                                                                                                                                                      |
| Robots.txt                             | A good read is kept 24 h. Parsed with Protego. A `User-agent: swingset` group wins over `*`, so any operator can stop or slow us without contacting us. 4xx (including 403, as on the WDR bucket) means unrestricted per RFC 9309. 5xx or unreachable: "disallow all" until a retry (below). |
| Request timeout                        | 30 s connect + read                                                                                                                                                                                                                                                                          |
| Retries                                | 3, full-jitter exponential backoff starting at 10 s                                                                                                                                                                                                                                          |
| 429 or 503 with `Retry-After`          | Honor it exactly, minimum 60 s                                                                                                                                                                                                                                                               |
| 429 or 503 without `Retry-After`       | Pause host for 15 min, doubling per repeat up to 24 h                                                                                                                                                                                                                                        |
| 403 or Cloudflare challenge            | Pause host 24 h. Log loudly. Never retry with different headers.                                                                                                                                                                                                                             |
| 404 on a watched URL                   | Mark watch `gone` after 3 consecutive 404s over 3 days; the registry's exact verified miss is lookup evidence instead (see its playbook)                                                                                                                                                     |
| Assets                                 | Never fetch images, CSS, JS, fonts                                                                                                                                                                                                                                                           |
| Compression                            | Send `Accept-Encoding: gzip`, always and only. Never brotli: Apache appends `-gzip` to ETags, so a changing encoding looks like a changed file, and not every client we run decodes brotli.                                                                                                  |
| Cookies                                | Not stored, not sent                                                                                                                                                                                                                                                                         |
| Per-source kill switch                 | `enabled = false` in config stops all fetches for that source                                                                                                                                                                                                                                |

A failed robots read (status 500 or above, including 599 for no response)
is read again when the host's ServerError backoff ends: 15 minutes after the
read, doubling with `hosts.pause_streak` to 24 h. Any other status that is
neither a 200 nor a 4xx also disallows all and is kept 24 h. A redirect
chain that ends without the file is not stored; the request is deferred.
Until the retry the fair scheduler leaves the host's watches out, and a pass
that left out a due watch of that host writes one `robots unavailable` host
guard. A long outage never turns into allow-all; the host stays closed
([D-0242](../../journal/decisions/0242-a-failed-robots-read-is-retried-on-the-host-backoff.md)).

The Wayback Machine replays a capture in its original encoding, whatever the
request accepts, so a body can arrive zstd-encoded. httpx decodes gzip and
deflate, but zstd only with the `zstandard` package, which is not installed.
The client decodes zstd with pyarrow inside the same byte limits and refuses
any other encoding httpx left undecoded
([limits](../../src/swingset/fetch/limits.py)). A refused response stores
nothing; it raises, and the acquire loop holds the watch on its
request-failure clock. Compressed bytes are never stored as a page.

The local schema-28 gate starts its next spacing interval after the preceding
response or failure completes, using the effective gap captured with that
request. This is a conservative implementation of the minimum: slow responses
reduce throughput. It preserves any later host deadline and shares live request
ownership across clients in the same writer. After restart, a retained known-gap
reservation receives a fresh full monotonic wait before another debit. Read-only
assessment never starts that recovery wait.

Legacy paid hosts have unknown original timing. Before operating the new gate,
the coordinator must record a reviewed stopped-worker baseline under the writer
lock, with a conservative gap justified by configuration and retained robots
evidence. `Gate.establish_spacing_baseline` records the evidence reference and
requires a fresh wait of at least 5 s, the floor migration 28 fixed in that
immutable table; it clears no pause, shortens no deadline and refunds no
usage. Hosts without a justified baseline remain blocked. See
[D-0056](../../journal/decisions/0056-anchor-host-spacing-to-request-completion.md)
and [current deployment status](../status.md).

A run also has a wall-clock budget ([operations](operations.md)). When time runs out,
remaining due watches wait for the next run. Nothing is lost because
"due" is computed from state, not from a queue.

The fair scheduler allocates this existing capacity; it does not increase
it. `scheduler_requests` attributes each issued HTTP request to its host,
UTC debit day, work class, and run in the same transaction that increments
`host_budget`. Robots checks, redirect hops, retries, and requests that
fail after issuance all count. A skipped, paused, or budget-denied visit
records no service. One captured grant timestamp owns eligibility against the daily budget, its
request debit and scheduler receipt. Response body bytes are added on release
to that original request day, even if dispatch or completion crosses midnight. These are accounted body bytes, not wire-byte measurements.

Pending-work backpressure is checked again at request admission. Its
two-request repair reserve includes every HTTP operation, so a robots
check and a redirect may use the whole reserve before the desired page
is reached. The next acquire tick can continue under the same host limits.
Reservation cannot override a source, kind, host, or global pause, a host
cooldown, a byte limit, or the Archive's 200-request daily budget. See
[fair scheduling](scheduling.md#work-order) for initial allocations and
the measured load behind them.

## Guards

A guard is a reason the client did not send a request
([D-0219](../../journal/decisions/0219-every-guard-that-holds-a-fetch-has-a-reason-with-an-expiry.md)).
`FetchClient.guards` counts each guard skip and `Paused` grant by subject and
reason. Each acquire tick's acquisition pass writes them to
`guard_reasons` in one transaction (`state/guards.flush`). A row keeps `since` and moves
`last_seen_at`, `seen` and `expires_at`. A subject that issued a request in the
pass (its host, its watch and the loop) loses its rows, and its skips in that
pass are dropped.

| Reason                                                                                                      | Subject | Expires                                                   |
| ----------------------------------------------------------------------------------------------------------- | ------- | --------------------------------------------------------- |
| `legacy request spacing unknown`, `request spacing recovery required`, `request spacing schema unavailable` | host    | `since` + 1 h                                             |
| `Throttled`, `ServerError`, `Blocked` (host pause)                                                          | host    | `hosts.paused_until` + 1 h                                |
| `request budget`, `byte budget`                                                                             | host    | next UTC midnight after `since` + 1 h                     |
| `operator`                                                                                                  | host    | the pause's `until_at`, or `since` + 48 h                 |
| `robots unavailable` (a failed robots read held a due watch; one row per host per pass)                     | host    | the robots retry + 1 h                                    |
| `pending work backpressure`                                                                                 | loop    | `since` + 12 h                                            |
| `event expansion pressure`, `event pressure metadata pending`                                               | host    | `since` + 24 h                                            |
| `publication_in_flight`, `release_disk_low`, `inputs_not_accepted` (plan, release)                          | loop    | `since` + 1 h                                             |
| `heavy_busy` (release)                                                                                      | loop    | `since` + 3 h                                             |
| `release_lag` (derive runs only parse and link units)                                                       | loop    | `since` + 1 h                                             |
| history gate reasons, `robots disallow`, `source disabled`                                                  | watch   | `since` + 30 d; written once a UTC day                    |
| `request_error` (the acquisition pass's choice raised; not contention)                                      | watch   | last failure + 1 h, doubling; 7 d from the third in a row |

Waits, deadline skips (`budget`, `budget or stop`), request limits and
terminal watch states (`sealed`, `archive required`, `capture already
archived`) are not guards. The scheduler leaves out watches it knows are
held (source off, host paused or over budget, a failed robots read before
its retry, operator pause, backpressure, event expansion), so those reasons
become rows only when a skip reaches the client; the spacing reasons always
do, and the pass writes `robots unavailable` itself. The watchdog alarms on a
host or loop row seen after its expiry within the last 2 hours
([operation](../guides/operation.md)); watch rows are counted only.
`swingset doctor` and `summary` report `guards`: counts by reason and the rows
seen past their expiry. A whole loop tick that skips leaves its own
`loop:<name>` row (`inputs_not_accepted`, `heavy_busy`, `release_disk_low`),
deleted by the next tick past the reason. A `request_error` row is written
when the failure happens, not by the flush. The flush leaves it, even for
a watch whose request went out before the error; the pass skips the watch
until the row expires, and a served request deletes it. A busy or locked
write, a write-deadline overrun, a publication fence, an operator pause and
a control-lock timeout skip the request and write nothing (F7).

A request admission carries the issuing run (the watch's run, or the
client's), so the loop's recovery settles one a killed tick left active.
An admission that waits past 90 s for `control.lock` (`ControlTimeout`) or
past the busy timeout for SQLite admits nothing: the fetch returns
`skipped: control_timeout` (counted in acquire's `control_timeouts`) or the
request waits for a later pass (F7).

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
  state, and gaps are owned by [backfill](backfill.md); the history
  plan makes no origin request (D-0236).
  Conflict resolution and observation ownership compare `observed_at`
  (capture time for archive bodies, fetch time otherwise), never our
  fetch time alone.
- WARC was considered. A plain content-addressed store plus SQLite is
  simpler and deduplicates better. A WARC export command can be added
  later without changing anything else.

## Registry check evidence (H1)

Every registry response records a verification attempt, including identical
200s, the exact verified 404 miss, 304s, invalid content, and transport
failures. Only a successful content check joined to an accepted interpretation
renews usable freshness. Repeated responses reuse the original observation
snapshot; they do not rewrite a claim's provenance or force a duplicate parse.
Checks received while parsing is pending keep their own check times and become
usable together when that interpretation succeeds. A 304 with a missing or
corrupt cached body records the failure and required digest, clears validators,
and does not count as a successful check. A missing or corrupt extract also
prevents freshness renewal. Automatic artifact repair is outside H1.
