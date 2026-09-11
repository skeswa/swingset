# EEPro (`eepro.com/results/`)

## 1. Status

Verified 2026-09-08 (headers in `research/verification/2026-09-08/eepro_*.hdr`).
The operator is a friend of the owner and has said an API is planned.
This playbook is a stop-gap until that API exists; the adapter is built
so the API can replace the fetch step without touching the parsers. No
terms of use page exists (checked 2026-09-04). `robots.txt` is 404,
which under RFC 9309 means unrestricted.

The owner reported on 2026-09-09 that permission was received on 2026-09-08.
No extra conditions or details of the conversation were supplied. Whether the
operator was told the User-Agent string, intervals, load estimate, and switch
in section 12 remains unverified.

## 2. What it gives

Per contest and round: competitor names, bibs, per-judge marks with
judge names, callbacks, finals ranks and placements, tie-break tallies.
No WSDC ids. Mostly US events (37 of 38 in the last year).

## 3. URL patterns

```
GET https://eepro.com/results/event.php                index, newest first (PHP, no validators, 27 KB)
GET https://eepro.com/results/event.php?event=<slug>   event page (PHP)
GET https://eepro.com/results/<slug>/                  Apache autoindex of the event's files (1.8 KB)
GET https://eepro.com/results/<slug>/<file>.html       one contest round, e.g. jjprelims.html, jjfinals.html
GET https://eepro.com/results/<slug>/<file>.pdf        occasional PDFs alongside
GET https://eepro.com/results/<year>/                  does not exist: 404 for 2012 and 2015 (2026-09-11)
```

`<slug>` is the operator's own key, e.g. `summerhummer2026`, `asc2025`,
`theopen2025`. It is not derivable from the WSDC name; it comes from the
index page. `/results/` itself is an empty HTML stub, not a listing.

File names seen: `jjprelims.html`, `jjfinals.html`, `aa.html`,
`routines.html`, `strictly.html`, `WSDCprelims.html`,
`wcsjjprelims.html`, `swingjjprelims.html`, `dancehalljjprelims.html`.
Treat the name as opaque; the contest and round come from the table
header inside the file.

## 4. Discovery

1. Poll `event.php` on the index schedule (section 6). Parse each row
   into a `source_events` record: slug, title, printed date.
2. Match to `events` by normalized name and date overlap. Unmatched
   rows still get an event with `wsdc_status = unknown`.
3. For each new slug create one watch of kind `index` on
   `/results/<slug>/` (the autoindex). Do not create round watches yet.
4. The autoindex parser creates one watch of kind `round` per `.html`
   or `.pdf` file it lists, carrying the listed mtime and size.

## 5. Change detection (verified)

- Round pages: strong `ETag` (`"236f5-659bbd82e9641"`, size and mtime)
  and `Last-Modified`. Conditional GET returns 304 with no body.
- `event.php` and `event.php?event=`: no validators, 200 every time.
  We do not poll the per-event PHP page at all; the autoindex replaces it.
- Autoindex: no validators, 1.8 KB, lists `Last modified` and `Size`
  per file. Comparing those columns against the stored values is the
  fast change signal for the whole event. It is not exact: the mtime
  has minute resolution and the size is rounded (`142K`), so a file
  corrected within the same minute as our last fetch, without changing
  its rounded size, would look unchanged forever. Round pages therefore
  also get a slow conditional refresh (section 7). Since the server
  answers 304 from the file's exact mtime and size, that refresh costs
  the operator a stat call and us a few hundred bytes.
- Apache appends `-gzip` to ETags when it compresses. We always send
  `Accept-Encoding: gzip`, so the suffix is stable.

## 6. Politeness settings

```toml
[hosts."eepro.com"]
min_gap_seconds = 5
daily_request_budget = 600
index_interval_weekend = "1h"      # event.php, Fri 00:00 to Mon 12:00 UTC
index_interval_weekday = "6h"
live_interval = "15m"              # autoindex only; doubles after 8 unchanged, max 1h
cooling_interval = "6h"            # doubles per unchanged check, max 24h
round_live_interval = "12h"        # conditional GET per round page while live
round_cooling_interval = "24h"     # conditional GET per round page for 30 days
```

The daily budget is a hard stop, not a target. Typical days use under
50 requests.

## 7. Fetch procedure

Per cycle, for each due watch on this host, in priority order live,
cooling, index, upcoming, archived, backfill:

1. `index` watch on `event.php`: plain GET. Parse, upsert
   `source_events`, create autoindex watches for new slugs.
2. `index` watch on `/results/<slug>/`: plain GET. Parse the listing.
   For each file whose mtime or size differs from the stored value,
   mark its `round` watch due now. Files not seen before get a new
   watch. Files that vanish are marked `gone` after 3 listings.
3. `round` watch: conditional GET with stored `ETag` and
   `Last-Modified`. On 304 record `checked_at`. On 200 archive the body,
   store validators, and queue the parser. Due when the listing entry
   changed, and on its own slow clock: every 12 h while live, every
   24 h while cooling, then the 90-day archived schedule. The clock
   exists because the listing's minute and rounded-size resolution can
   hide a correction; the ETag cannot.

The autoindex carries the fast timer; round pages only carry a slow
conditional one. That is why the per-event cost is small.

## 8. Parsing

`eepro.round` (from `design/parsing.md`, verified 2026-09-09 fixtures):

- One `<table border="1">` per contest. The first row is one spanning cell
  ending in `<Prelims|Finals>` (older pages can prefix it with `Division:`);
  the contest name and round come from it, never from the file name.
- Prelims columns: `Count`, `Competitor`, one column per judge (header
  is the judge's name; values `Y`, `A1`, `A2`, `A3`, `N`), `BIB`,
  `Counts` (`Y-A-N`), `Sum`, `Promote` (`X` for callback), `Alt`.
- Finals columns: `Place`, `Competitor` (`Leader and Follower`), one
  column per judge (rank), `BIB` (`255/720` for J&J is leader/follower;
  a single number for couples), `Marks Sorted`.
- Read columns by header text. Judge count varies per contest.
- Store marks as printed in `mark_raw`; map `Y`/`A1`/`A2`/`A3`/`N` to
  the shared enum.
- `eepro.index`: rows of `event.php`, slug from the `event=` query.
- `eepro.autoindex`: Apache listing table, columns `Name`,
  `Last modified` (`YYYY-MM-DD HH:MM`, server local time, treat as
  opaque text and compare for equality), `Size` (`8.5K`, `142K`).

`Count` is the ordinal standing after the callback marks are totaled, not a
heat number. Equal sums share a count (for example, three perfect scores all
print `1`), later counts skip the tied positions, and entries below the
promotion cutoff can have a blank count. This was verified in Summer Hummer
2026 `jjprelims.html` on 2026-09-09.

Summer Hummer 2026 `aa.html` labels its All American 0–100 judge scores as
`Prelims` and adds `Avg` and `Place` columns. The canonical model has no numeric
scoring method for that layout. We retain the raw table, publish its contest as
`unsupported`, and omit derived rounds, marks, and score aggregates rather than
misrepresenting the numbers as callback marks.

## 9. Quirks

- Two calendar rows can map to one slug (Flow Festival NYC 2026 was
  listed twice on the WSDC calendar).
- Some events point at a round page or the bare directory rather than
  `event.php` (Freedom Swing 2026, Wild Wild Westie 2026). The autoindex
  path handles both.
- Round pages of large prelims are 145 KB; the whole event is under
  1 MB.
- The "SwingDancer" app posts callbacks; we do not touch it.

## 10. Backfill

The Wayback Machine holds 1,124 distinct 200-status URLs under
`eepro.com/results/` across 148 slugs, captured 2018 to 2026 (slugs by
year: 2018: 8, 2019: 16, 2020: 1, 2021: 6, 2022: 17, 2023: 19,
2024: 31, 2025: 36, 2026: 13). Nothing older is archived. Order:

1. One paged CDX query per capture year of `eepro.com/results/*`
   (`design/backfill.md`); rows land in `archive_captures` and become
   `backfill` watches.
2. Fetch from the archive at the Wayback host's own settings, event
   pages before round pages.
3. A slug goes to the origin, newest first, lowest priority, one event
   per cycle, when the archive lacks a listed file or the archived copy
   does not parse into results.

Year indexes do not exist (404, 2026-09-11). The origin still serves
old slug directories (`/results/liberty2018/` is a 2.2 KB index page),
but `event.php` lists only 2024 on, so slugs before 2018 are learned
from the operator, never by probing.

## 11. Load estimate

Per event weekend: about 150 autoindex polls (1.8 KB each), about 40
round fetches on listing change, and about 250 clocked conditional
refreshes (20 files, twice a day for six days, then daily for 30 days),
almost all 304s. Under 4 MB and under 500 requests over the live window,
all static file reads. Off-season: 4 index polls a day.

## 12. Operator switch

Add to `https://eepro.com/robots.txt`:

```
User-agent: swingset
Disallow: /
```

We stop within 24 hours. A `Crawl-delay: 30` line in the same group
slows us to one request per 30 s instead. Either works without telling
us, though a GitHub issue is welcome.

## 13. Open items

- Confirm with the operator that autoindex listings are intentional and
  will stay on; if they turn `Options -Indexes` on, fall back to
  polling `event.php?event=<slug>` (27 KB, no validators) at the same
  intervals.
- Ask whether an `index.json` or a `Last-Modified` on `event.php` is a
  cheap favor; either removes the last unconditional fetch.
- Ask the operator for the list of slugs older than 2018 that the
  origin still serves, or for the API, before any pre-2018 fetch.
- Measure a live event weekend; the accepted historical run does not establish
  live request volume or conditional-response rates.
