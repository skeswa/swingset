# Wayback Machine (`web.archive.org`)

## 1. Status

Verified 2026-09-11 (`research/verification/2026-09-11/`). The Internet
Archive is a nonprofit library, not a results platform; we read it so
that we do not re-crawl the sites it already saved. No personal
relationship. `robots.txt` is 404. The terms page
(`archive.org/about/terms`) needs JavaScript and has not been read by
the pipeline; the owner reads it by hand before backfill starts and
records the date here. **Unverified**: the commonly quoted clause that
access is for scholarship and research purposes.

## 2. What it gives

Captures of every source we read, with the origin's original bytes and
headers: EEPro from 2018, DCN from 2017, scoring.dance from 2021, WDR
from 2022, Step Right Solutions round pages for events from 2009, and
the WSDC calendar in scattered months from late 2016. Numbers are in
`research/wayback-coverage-2026-09-11.md`.

## 3. URL patterns

```
GET https://web.archive.org/cdx/search/cdx?url=<prefix>*&from=<yyyy>&to=<yyyy>&filter=statuscode:200&fl=timestamp,original,digest,mimetype,length&collapse=digest&output=json&page=<n>
GET https://web.archive.org/cdx/search/cdx?url=<prefix>*&...&showNumPages=true
GET https://web.archive.org/web/<timestamp>id_/<original url>
```

Not used: `web/<timestamp>/<url>` without `id_` (rewritten HTML), Save
Page Now, the availability API, timemaps.

## 4. Discovery

`design/backfill.md` owns it. Per source, per capture year, one paged
CDX query over the prefixes in that source's playbook section 10. Rows
land in `archive_captures`; watches are made from them. Event-site
prefixes are queried for PDFs and results-like HTML, and hits go to the
review queue as suggested override rows.

## 5. Change detection

Captures are immutable. A fetched `(url, timestamp)` is never fetched
again. CDX queries for a finished year are made once; the current and
previous year are repeated on a 90-day clock.

## 6. Politeness settings

```toml
[hosts."web.archive.org"]
min_gap_seconds = 10
daily_request_budget = 200
```

Timeouts differ from the defaults: 120 s for CDX, 30 s for bodies. CDX
queries over large prefixes took 8 to 24 s and three timed out at 30 s
on 2026-09-11; the fix is narrow `from`/`to` windows and paging, not
retries. Raising the daily budget to 400 is an operator decision after a
week without a 429 or 503, recorded here with the date.

## 7. Fetch procedure

1. CDX pages for the due source-year, one page per request.
2. For each URL, select a capture (`design/backfill.md`, "Selecting a
   capture"), fetch with `id_`, follow in-archive redirects through the
   gate (at most 3 hops), store under the original URL with
   `via = wayback`, `captured_at` from `memento-datetime`.
3. Extract, fingerprint, parse as the live path does. Incomplete parse:
   next-latest distinct capture, at most three, then a gap finding.
4. Backfill fetches run only when nothing else is due (priority 6).
   Calendar captures are index work (priority 2).

## 8. Parsing

None of its own. Bodies are parsed by the source's page kind. The
transport records `x-archive-orig-*` headers so the origin's validators
at capture time are known.

## 9. Quirks

- `id_` responses carry `memento-datetime`, `x-archive-src` (the WARC),
  a `link` header with first, prev, next, and last mementos, and
  `X-RL` and `X-NA` headers of **unverified** meaning.
- A capture can be stored under a timestamp that differs from the CDX
  row by seconds; the redirect is inside the archive.
- Some captures are compressed with zstd rather than gzip in the
  archive's own storage; the 2026-08-13 calendar capture was skipped in
  research for this reason. Whether `id_` fetches serve such captures
  decoded is **unverified**.
- CDX `collapse=digest` merges only adjacent identical captures; the
  selection step deduplicates by digest again.

## 10. Backfill

This host is the backfill. Its own history is not backfilled.

## 11. Load estimate

About 8,000 requests in total for the 2010 start (`design/backfill.md`,
"Scheduling"), then a few hundred a year for new captures of the
current and previous year. Bodies are small: round pages are under
150 KB, DCN pages 1.67 MB gzip.

## 12. Operator switch

No robots group exists to honor. A 429 or 503 pauses the host by the
default rules; a 403 pauses it for 24 h and alerts the owner. If the
Internet Archive asks us to stop, by any channel, backfill stops that
day and the card says so.

## 13. Open items

- Read the terms by hand and record the date.
- Meaning of `X-RL` and `X-NA`.
- Whether DCN `roundscores/*.pdf` captures exist.
- Whether zstd-stored captures come back decoded through `id_`.
