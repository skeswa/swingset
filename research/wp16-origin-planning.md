# WP16 origin-gap planning

The bounded offline planner is `history.origin.propose_origin`. It returns an
`OriginProposal` from an existing canonical event binding, its exact known page
URL, retained captures and their interpretation outcomes, completed CDX query
receipts, and the current shared year/page-kind gate result. It creates no
watch, request, finding row, policy activation, or year acceptance.

An empty local capture list is insufficient. Every capture year from
`history_start` through the current year needs a completed, correctly scoped CDX
receipt with all pages consumed. A zero-page response is valid evidence; a
missing response, failed page, unrelated prefix, or partial query is not. Current
and previous-year receipts expire on the existing 90-day refresh clock; older
completed queries do not. No query is made by this planner.

A retained usable archive copy always takes precedence, including an older copy
outside the preferred three and an HTTP spelling of the same HTTPS resource.
An untried capture, pending interpretation, or retryable transport failure keeps
origin blocked. Only when the preferred distinct alternatives (at most three,
or all available when fewer exist) have actually produced incomplete results
can the documented unusable-copy fallback be proposed. Completed index coverage
is still required, so an interrupted CDX query cannot conceal an alternative.

This follows the source-specific rules: [EEPro section 10](../docs/sources/eepro.md#10-backfill)
permits origin for a missing listed file or a copy that does not parse;
[scoring.dance section 10](../docs/sources/scoring-dance.md#10-backfill) permits
missing events or incomplete round coverage; [DCN section 10](../docs/sources/danceconvention.md#10-backfill)
uses complete archived results/PDFs and caps remaining origin work at one event
per day. Proposals retain a one-event-per-cycle cap, and DCN additionally carries
its one-event-per-day cap. These fields describe the execution limit; they do
not consume or establish an execution allowance.

EEPro events before 2018 require an explicitly operator-supplied known source
reference. No slug guessing or probing is introduced. Unaccepted or stale years,
unassessed or non-enforcing page kinds, unmapped events, and events before the
2010 history floor remain blocked. WDR and Step Right have no added origin
fallback. New-source fixture exceptions remain separate from all production
acquisition gates.

## Integrated runtime (future schema 15)

The normal history dispatcher now calls this planner. `backfill.offer(...,
run_id=...)` offers a known origin page only after retained archive proof passes;
`dispatch_one` repeats that decision in a write transaction. Eligible archive
work of the same source takes precedence. The existing priority-6 fair allocation
uses the offered URL's actual host, rather than charging origin work to Archive.
No new source, page-kind policy, year acceptance, or scheduled intake is enabled
by this implementation.

Migration 15 adds three initially empty tables. `history_origin_intents` records
the original URL, actual event mapping, parent URL, query/capture proof and the
current dispatch run. `history_origin_requests` records actual HTTP admissions,
including robots and redirects, in the same transaction as the host-budget
charge. It limits each source to one event per cycle, and DCN to one event per UTC
day. Waiting, paused, budget-depleted and rejected requests consume neither a
request nor an event allowance. Existing host budgets, bytes, cooldowns,
conditional validators and operator controls remain authoritative. An Archive
host pause does not block a proved gap on an independently healthy origin host.

The fetcher rechecks the exact year/page-kind gate, mapping and archive proof
before any robots/dedup work, after every wait and before each real request.
Only the original resource, its HTTP/HTTPS spelling, and that host's robots file
are allowed; a redirect cannot discover a sibling page or another host. Managed
origin controls are excluded from ordinary polling and require the dispatcher's
run reservation. Existing independent origin controls retain their own cadence
and are never converted into a second origin queue. Historical origin parents
retain declared child URLs and FileRows as evidence; they do not create or
refresh ungated live child controls.

`origin_dispatch.record_operator_reference` records a retained operator-response
reference for an already mapped EEPro source reference, within the coordinator's
write transaction. It cannot invent a mapping, create a watch, admit a parser,
or accept a year. No operator response has been recorded by these tests or this
implementation.

Offline validation includes a real retained EEPro index with seven child files,
an actual cycle using fake HTTP and clock, paused-child preservation, atomic
planning rollback, actual origin-host budget and spacing checks, policy/capture
changes after robots, redirect refusal, persisted event cadence, and migration
preservation of every preexisting table row. All acquisition tests use mock
transport; no source request was made.

Production remains on the separately pinned schema-14 runtime until a reviewed migration/deployment. All 17 years
remain unaccepted. Actual origin intake, DCN history-year index/parser preparation
and real fixture admission, EEPro's operator answer, and generic event-site
interpretation remain separate WP16 completion items. Nothing here writes an
override file or treats a model proposal as human review.

## Event-site candidates from retained CDX evidence

`history.event_sites` supplies the event-site review path described in backfill.
The existing reusable runtime is the finding-to-`review_queue` path and its
`suggested_override` field. The live long-tail link scan and the documented
`generic.html_table` / `generic.pdf_table` adapters are not implemented; this
change does not claim otherwise.

The planner binds a canonical event website to its exact site prefix and checks
only the event year and following capture year. `query_urls` returns four
**first-page query specifications**: PDF and HTML for each year, with HTTP-200,
MIME and HTML result-path filters. It does not submit a query. The local importer described below validates retained
filtered page-count probes and page receipts. The explicitly callable executor below now supplies shared host accounting and
bounded acquisition. It remains disabled and is not scheduled by the cycle. No broad site query is issued.

A proposal requires a retained CDX page receipt using the exact supported Archive
endpoint, port, field projection, filters, prefix and capture-year window. Its
body hash is verified and every proposed capture must also exist in the retained
capture inventory. Findings retain the query URL, receipt/body hashes, original
URL and capture timestamps. HTML paths must contain `result`, `score`, `callback`,
`prelim` or `final`; query-string words do not count. Missing evidence produces an
explicit gap, never proof that the archive has nothing.

Adjacent editions sharing a website remain ambiguous unless an explicit URL
year leaves one candidate. Ambiguity, conflicting formats or an existing override
for another event produce findings without a CSV row. HTTP URLs are not rewritten
into guessed HTTPS targets. A unique HTTPS candidate receives a deterministic
**draft** `source_urls.csv` suggestion. Generic parser availability is disclosed;
unavailable adapters still cause normal override validation to reject an attempted
installation. Human review, implemented and admitted parsers, year acceptance and
actual acquisition remain separate steps. This helper never writes an override
file, watch, source observation, acceptance record or request.

Cycle reconciliation processes at most ten accepted event sites per invocation,
rotating a durable cursor. Each event permits at most 64 retained page receipts,
each body is capped at 8 MiB, and review has a ten-second wall-clock allowance.
Body inspection happens outside the write transaction so controls can pause a
running review. The exact edition set and year acceptance are checked again
before installing findings. All/kind controls hold new review operations. A
reviewed override closes its suggestion; it does not itself start acquisition.
The card reports only public finding counts and identified years, without
echoing private notes or proposed URLs.

This completes the retained event-site **suggestion** path. Actual generic
HTML/PDF interpretation and event-site acquisition remain unimplemented. The
local retained PDF fixtures are newsletters, not score sheets; they cannot serve
as real score-PDF admission controls. DCN likewise retains CDX/headers but no
complete metadata/results/PDF fixture, so its history index/parser and PDF
interpretation remain pending the reviewed fixture exception and available
archive quota. No actual source request or review decision was made here.

## Local filtered-CDX evidence intake

`history.event_site_intake.ingest` imports a finite local package of already
retained CDX responses. Its default is `dry_run=True`. A coordinator with an
existing locked `Database` can call:

```python
from pathlib import Path
from swingset.history.event_site_intake import ingest
from swingset.history.event_sites import Site

result = ingest(
    database,
    Site(event_id, event_year, exact_canonical_website),
    directory=Path("/path/to/retained-package"),
    manifest_sha256=reviewed_package_sha256,
    now=clock.now(),
    dry_run=True,
)
```

Explicit `dry_run=False` retains verified evidence for the existing cycle review
path. Both modes require the exact canonical website/year, accepted-year proof,
the 2010 history floor, and unpaused source/mapping controls. Import is not
page-kind activation: there is no generic scoring parser, source observation,
watch, HTTP request, budget debit or year-acceptance write. Host-only pauses do
not block this offline operation. The scheduled-service filesystem hold is not
modified. The caller owns the normal writer lock; this API never opens or
migrates another database.

The package contains `event-site-intake.json` with this shape:

```json
{
  "format": "event-site-cdx-intake-v1",
  "event_id": "the-existing-canonical-event-id",
  "year": 2019,
  "website": "https://the-exact-retained-event-website/",
  "queries": [
    {
      "query_id": "original-64-character-query-identifier",
      "receipts": [
        { "page": "probe", "sha256": "hash-of-original-probe-receipt-bytes" },
        { "page": "0", "sha256": "hash-of-original-page-zero-receipt-bytes" }
      ]
    }
  ]
}
```

These are explanatory placeholders, not authorized query targets. Receipt files
are `archive-cdx/<query_id>/<page>.json`; bodies use the existing compressed
`Archive` blob layout. Each receipt supplies its original query ID, page, exact
request URL, aware fetch time, HTTP status, headers and body SHA256. The manifest
hash binds the original receipt hashes, which bind the bodies. Import preserves
receipt bytes exactly and retains the pinned manifest under
`history/event-site-intakes/<sha256>.json`.

Each query starts with its filtered `showNumPages=true` response, followed by a
contiguous prefix of pages. `query_url(site, capture_year, mime, page=None)` gives
the exact probe specification; an integer selects that page. PDF and filtered
HTML use distinct query IDs. Probe/page filters, website prefix and capture year
must agree. The only permitted years are the event year and the next. Requests
with extra fields, duplicate filters, credentials, nonstandard ports or another
endpoint are rejected before capture inventory writes.

A partial package resumes with the identical probe and already supplied pages.
A refresh needs a different query ID; old receipt bytes cannot be replaced.
Duplicate URL/time captures keep their first database pointer, while every query
retains its independent original page receipts. Conflicting capture content is
rejected. Review validates each page against that query's receipt and verifies
capture inventory membership separately, so a repeated hit does not disappear
because it first arrived in another query. All rows use `source=event_sites`;
filtered discovery can never establish unfiltered platform-origin absence.

Bounds are four filter/year queries, 64 pages plus at most four probes, 8 MiB per
compressed or uncompressed body, 64 MiB total uncompressed bodies, 128 KiB per
receipt/manifest and at most 60 seconds (30 by default). Existing destination
files obey the same bounded checks. Receipt files become durable before one
atomic metadata transaction. Interruption can leave unreferenced immutable
artifacts; it cannot commit partial query metadata. Retry validates and reuses
those artifacts. The exact event and acceptance/control gates are checked again
before commit. The ordinary bounded `event_sites.reconcile` then produces draft
review findings; it does not fetch the discovered pages.

Offline tests cover raw-byte preservation, empty probes, partial resume,
idempotence, repeated capture provenance, filter/refresh collisions, corrupted
or oversized evidence, namespace isolation, pause/year changes and transaction
rollback. This closes local retained-evidence intake. The disabled executor below can collect
compatible evidence through the shared gate. Actual intake activation, generic
score HTML/PDF interpretation, human override review and acquisition of discovered
pages remain separate, unfinished work.

## Disabled filtered-CDX executor

`history.event_site_queries.execute` is explicitly callable but not registered
with the ordinary cycle. The current source configuration has no enabled
`event_sites` source. Its request flag defaults to false. No source configuration,
cycle activation, policy, year approval or real request was changed during this
implementation.

```python
from swingset.history.event_site_queries import execute

result = execute(
    fetch_client,
    site,
    year=site.year,
    mime="application/pdf",
    run_id=existing_coordinator_run_id,
    execute_requests=False,
)
```

An actual call requires separately enabled source configuration, the existing
state lock and run, explicit `execute_requests=True`, and a coordinator-owned
**external hard timeout**. The in-process deadline is cooperative: HTTPX read
and connect timeouts apply to blocking operations, so a slow blocking read can
overrun the remaining allowance before the next chunk check. `QueryResult`
reports that limitation. Do not interpret its 60-second allowance as a process
kill deadline. A response retained after the allowance is not selected into
query metadata; the next invocation can reuse it after validation.

One invocation handles one full filter/year recipe. The recipe contains the
canonical event ID/year/website and exact filtered probe URL; PDF, HTML and
capture-year cursors cannot share an identity. An immutable intent is retained
before the first request. Older completed queries are not repeated. Current and
previous capture years retain the existing 90-day refresh clock; refreshes get
new IDs and preserve all older receipts. Completed local receipts are checked
before reuse, including after an interrupted metadata commit. No successful
probe or page needs another request merely because its SQLite update was lost.

The existing platform `FetchClient.index_archive` delegates to the moved
`fetch.cdx` workflow with the same unfiltered behavior. Both workflows use its
request and response-receipt helpers. The unfiltered entrypoint rejects the
reserved `event_sites` namespace. The filtered workflow is separate only where
its canonical-event gate, filter recipe and durable resume proof differ.

An optional request context supplements the existing request, parent/history,
robots, admission and host gates. Before each actual debit, including robots,
redirects and retries, it rechecks the current website/year, accepted inventory
proof, enabled source, mapping/source pauses, request ceiling and deadline. A
pause or evidence change during robots or polite waiting prevents the next
HTTP debit. All requests are limited to the exact HTTPS Archive CDX recipe or
Archive's robots file. Redirects cannot fetch a replay capture, sibling CDX
query, another host or any event-site body. Discovered score URLs are never
requested.

The executor requires Archive's ten-second floor and a configured daily request
budget of at most 200. Existing depleted budgets, byte budgets, cooldowns and
one-request-per-host control remain authoritative. Its virtual work is recorded
as priority-six `old` acquisition, including robots/redirect/retry debits, and
cannot consume the repair reserve under backpressure. No watch is inserted.

Each invocation permits at most eight admitted request attempts and 64 declared
pages. `admitted_attempts` counts charged/admitted attempts, not a claim that every
HTTP request reached its server; `uncertain_admissions` identifies retained
charges after an acknowledgment failure. Transport failures do not refund
charges. Wire and decoded counters are separate, with an 8 MiB response limit
and 64 MiB invocation limit. Streaming checks each underlying transport chunk without output batching; detecting excess may
consume one additional wire chunk or one decoded guard byte, which is charged
and retained only as failed evidence. Existing callers without a bounded context
keep their original response path.

Only identity or single-member gzip encoding is supported in this bounded path.
Gzip output is decompressed with a remaining-size limit; incomplete streams,
trailing members/data, unsupported encodings and excessive bodies cannot become
selected evidence. The original response headers remain in the receipt even
though the stored body is the decoded representation, matching the archive's
existing body convention. Partial/error responses and retries are retained under
`archive-cdx/<query_id>/diagnostics/` with explicit incomplete reasons. A valid
HTTP status alone does not override those flags. Import and review both reject
explicitly incomplete or failed receipts, even if the retained prefix parses as
JSON. Only validated complete probe/pages enter the local intake manifest and
atomic capture/query inventory.

Offline execution tests exercise shared quotas and spacing, source/kind/host
holds, changes during robots/waits/actual admission, retry ceilings, decompression
limits, redirect scope, interrupted metadata resume, filter separation, refresh
provenance and priority-six attribution. All HTTP is mocked. This supplies a
reviewable executor implementation; ordinary-cycle activation, actual CDX
execution and generic scoring interpretation remain undone.
