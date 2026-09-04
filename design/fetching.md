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

All values are per host unless stated. Defaults live in
`config/hosts.toml` and may be tightened per host, never loosened below
the floor.

| Rule | Value |
|---|---|
| In-flight requests per host | 1 |
| Minimum gap between requests to a host | 5 s (floor). 2 s for the registry sweep only, because responses are tiny and the site is built for this. |
| `Crawl-delay` in robots.txt | Honored if larger than our gap. |
| Hosts fetched in parallel | at most 4 |
| Daily request budget per host | 2,000 (registry: 20,000 during bootstrap, 1,500 after) |
| Robots.txt | Fetched at most every 24 h. Parsed with Protego. 5xx or unreachable means "disallow all" until next check. |
| Request timeout | 30 s connect + read |
| Retries | 3, full-jitter exponential backoff starting at 10 s |
| 429 or 503 with `Retry-After` | Honor it exactly, minimum 60 s |
| 429 or 503 without `Retry-After` | Pause host for 15 min, doubling per repeat up to 24 h |
| 403 or Cloudflare challenge | Pause host 24 h. Log loudly. Never retry with different headers. |
| 404 on a watched URL | Mark watch `gone` after 3 consecutive 404s over 3 days |
| Assets | Never fetch images, CSS, JS, fonts |
| Compression | Send `Accept-Encoding: gzip, br` |
| Cookies | Not stored, not sent |
| Per-source kill switch | `enabled = false` in config stops all fetches for that source |

A run also has a wall-clock budget ([operations](operations.md)). When time runs out,
remaining due watches wait for the next run. Nothing is lost because
"due" is computed from state, not from a queue.

## Change detection

Order of preference:

1. **Conditional GET.** Send `If-None-Match` with the stored ETag and
   `If-Modified-Since` with the stored `Last-Modified`. A 304 costs a few
   hundred bytes and records `checked_at` only.
2. **Body hash.** On 200, compute SHA-256 of the raw body. If it equals
   the last stored hash, treat as unchanged. Store nothing new except
   `checked_at`.
3. **Normalized hash.** Some pages embed nonces, timestamps, or session
   ids. Each parser may define a `fingerprint(body) -> bytes` that strips
   volatile parts before hashing. If the normalized hash is unchanged we
   still store the raw body (cheap, deduplicated) but mark the snapshot
   `content_changed = false` so downstream skips it.
4. **HEAD** is not used. A conditional GET is cheaper than HEAD plus GET
   and the same size as a HEAD when unchanged.

The registry's POST endpoint supports none of this. Each refresh is a
full fetch of a few KB. The schedule (5.2) keeps those rare.

## Archive

- Blob store: `blobs/sha256/<aa>/<bb>/<hash>` holding the raw body,
  gzip-compressed if not already compressed. Identical bodies are stored
  once.
- `snapshots` table: `snapshot_id`, `watch_id`, `url`, `fetched_at`,
  `http_status`, `etag`, `last_modified`, `content_type`, `body_sha256`,
  `body_bytes`, `content_changed`, `run_id`. Response headers stored as
  JSON.
- We keep every changed snapshot forever. Storage is small: a few MB per
  event weekend after compression and dedup.
- WARC was considered. A plain content-addressed store plus SQLite is
  simpler and deduplicates better. A WARC export command can be added
  later without changing anything else.
