# Wayback Machine (`web.archive.org`)

## 1. Status

Verified 2026-09-11 (`research/verification/2026-09-11/`). The Internet
Archive is a nonprofit library, not a results platform; we read it so
that we do not re-crawl the sites it already saved. No personal
relationship. `robots.txt` is 404. The owner reported reading
`https://archive.org/about/terms` on the afternoon of 2026-09-12
(America/Denver), before bulk backfill intake. This records the required
manual review; the pipeline did not extract the page. **Unverified**:
the commonly quoted clause that access is for scholarship and research
purposes.

## 2. What it gives

Captures of every source we read, with the origin's original bytes and
headers: EEPro from 2018, DCN from 2017, scoring.dance from 2021, WDR
from 2022, Step Right Solutions round pages for events from 2009, and
the WSDC calendar in scattered months from late 2016. Numbers are in
`research/wayback-coverage-2026-09-11.md`.

## 3. URL patterns

```
GET https://web.archive.org/cdx/search/cdx?url=<prefix>*&from=<yyyy>&to=<yyyy>&filter=statuscode:200&fl=timestamp,original,digest,mimetype,length&collapse=digest&output=json&page=<n>
GET https://web.archive.org/cdx/search/cdx?url=<prefix>*&from=<yyyy>&to=<yyyy>&showNumPages=true
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

- Owner's manual terms review recorded above: 2026-09-12.
- Meaning of `X-RL` and `X-NA`.
- Whether DCN `roundscores/*.pdf` captures exist.
- Whether zstd-stored captures come back decoded through `id_`.

## 11. Implemented phase 1 interface

`FetchClient.index_archive` runs the bounded CDX query through the project
robots, host gap, budget, and retry gate. It checkpoints each page. Calling
it again resumes unfinished work; completed current and previous-year
queries refresh after 90 days. Older completed queries are retained.

The Python interface below uses a separate state directory. It performs
live requests when executed; use it only for an approved phase 1 run.
The configured source must be enabled. The archive host settings stay at
10 seconds and 200 requests per day.

```python
from datetime import timedelta
from pathlib import Path

from swingset.clock import SystemClock
from swingset.config import load_config
from swingset.fetch.archive import Archive
from swingset.fetch.client import FetchClient
from swingset.fetch.wayback import replay_url, schedule_capture
from swingset.schedule.parse import parse_snapshot
from swingset.sources import get_page_kind
from swingset.sources.base import WatchSpec
from swingset.state.db import open_database
from swingset.state.work import WorkUnit

clock = SystemClock()
with open_database(Path("/tmp/swingset-history-phase1")) as database:
    archive = Archive(database.state_dir)
    run_id = database.start_run(clock.now(), dry_run=True)
    client = FetchClient(database.connection, load_config(), clock, archive)
    try:
        client.index_archive(
            source="wsdc_calendar",
            prefix="worldsdc.com/events/",
            year=2019,
            deadline=clock.now() + timedelta(minutes=5),
        )
        captures = database.connection.execute(
            "SELECT url,timestamp FROM archive_captures "
            "WHERE source='wsdc_calendar' ORDER BY timestamp,url"
        ).fetchall()
        for url, timestamp in captures:
            spec = WatchSpec(
                "",
                "wsdc_calendar",
                "index",
                "GET",
                url,
                "wsdc_calendar.events",
                archive_url=replay_url(url, timestamp),
            )
            if not schedule_capture(database.connection, spec, now=clock.now()):
                continue
            fetched = client.fetch(spec.watch_id, get_page_kind(spec.parser), run_id)
            if fetched.snapshot_id:
                parse_snapshot(
                    database,
                    archive,
                    WorkUnit("parse", "snapshot", fetched.snapshot_id),
                    clock,
                    run_id,
                )
    finally:
        client.close()
```

Parsing queues projection work for the normal cycle. Index observations
from different captures remain available together under the one original
URL watch. A successfully parsed capture seals its watch;
`schedule_capture` selects another unprocessed capture explicitly. An
archive index does not create child score-sheet watches.

`project.history.accept_year` records an explicit named owner's sign-off
only after the year's event-list findings are resolved. Changes to the
event inventory invalidate that sign-off. Phase 2 watch insertion also
requires the recorded H7 and H10 deployment gates. These interfaces do
not make a stage accepted or authorize publication.

Offline fixtures cover CDX paging and refresh, capture provenance,
observation time, bounded fallback after parse failures, and sealing.
The 2012-08-25 council capture and newsletter Volume 6 were read through
the gate on 2026-09-13 UTC and are retained with request metadata. The
FreedomSwing 2019 event-index acceptance read completed on 2026-09-13 UTC
through the persistent archive gate: 1,977 bytes, five result locators,
and no child watches or score-sheet reads. Its full receipt and body
are in `research/workflow-output/v2-phase1/wp11-eepro2019/`;
`research/accept_wayback_event.py` reproduces the bounded check.
Remaining phase 1 captures, per-year owner acceptance, and published
coverage are tracked separately.

## 12. Finite phase 1 intake

`research/intake_phase1.py` is the reproducible orchestration entry point.
It accepts only event-list parsers. Run it with the project's Python
and `PYTHONPATH=src`; it creates no historical score-sheet watches and
never publishes.

```sh
python research/intake_phase1.py bootstrap \
  --checkpoint /path/to/read-only-checkpoint --state /path/to/intake
python research/intake_phase1.py catalog --state /path/to/intake
python research/intake_phase1.py run --state /path/to/intake \
  --max-targets 180 --wall-seconds 3000
python research/intake_phase1.py run --state /path/to/intake \
  --retry-failures --max-targets 180 --wall-seconds 3000
python research/intake_phase1.py discover --state /path/to/intake --wall-seconds 900
python research/intake_phase1.py run --state /path/to/intake --max-targets 180
python research/intake_phase1.py project --state /path/to/intake
python research/intake_phase1.py review --state /path/to/intake \
  --output research/workflow-output/v2-phase1
```

The database is copied from the checkpoint. Immutable blobs, extracts,
and captured inputs may be hardlinked on the same filesystem; new
artifacts use new paths. Mutable database, locks, and journals are never
linked. Resume the same intake directory so host budgets and pause
windows persist. Do not run a second intake to bypass an exhausted
budget. Include any earlier fixture requests in that host's daily
budget before starting.

The initial retained-CDX catalog has 152 archive targets: 41 council
captures and 111 query-free captures across the five calendar paths.
Five council targets share an earlier digest, so the initial archive
body budget is 147 before existing-body reuse, robots, and redirects.
The newsletter index adds its actual PDF links. Queries/captures after
2023 are additional work and must fit the same daily request budget.

Every catalog target ends as parsed, empty, a duplicate of retained
successful evidence, an explicit finding, or pending at the budget
boundary. Parser changes reparse cached successful captures; failures
are retried explicitly. Transport diagnostics do not masquerade as
successful immutable captures. Run `review --no-reconcile` for a
read-only progress report while the intake owns the writer lock; it
reports pending projection work and cannot approve a year.

Use `export --state /path/to/intake --package /path/to/package` after
intake has stopped. It exports only catalog evidence and its immutable
bodies/extracts, plus CDX receipt bodies, query checkpoints, and host
budget receipts. It never exports canonical tables or live controls.
After the correction release, the owner can run
`import --state /path/to/settled-state --package /path/to/package`,
then `project` and `review`. Import preserves existing watch controls,
retains live observations, and merges budgets/query cursors monotonically.
Repeating the same import does not duplicate snapshots or requests.
Never replace the settled production database with the intake baseline.

The page-count probe omits capture fields and JSON row output. The
[official CDX pagination API](https://github.com/internetarchive/wayback/blob/master/wayback-cdx-server/README.md#pagination-api)
returns a count; projecting capture fields on the current service
instead produced an all-null row, retained in the intake's CDX receipts.

Import uses one outer database transaction, including cached parsing and
restoring existing watch controls. A crash rolls back the whole evidence
merge. Catalogs are merged in memory before one durable replacement.
Budget `MAX` merging requires a shared checkpoint and exclusive archive
host ownership during intake, as used for this run. Independently active
fetchers require request-receipt reconciliation before importing budgets.
