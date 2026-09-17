# Proposing a bounded source-fixture exception

> Historical record. Dates, status claims, and instructions below describe the
> original work. See [current status](../../../docs/status.md) before acting.
> [Project journal](../../README.md)

Status: **proposal, not authorized or executed**. Prepared on 2026-09-13 UTC
from retained research only. No request was made to prepare this packet.

Current decision, 2026-09-17: the owner approved this exact exception in
[D-0053](../../decisions/0053-approve-exact-new-source-fixture-exception.md).
[D-0054](../../decisions/0054-keep-step-right-history-at-the-2010-floor.md)
resolved WP14 to 2010–2016. The original proposal below remains a historical
record; its request allowlist and limits are unchanged.

## Proposed owner decision

After V2/V4 publish, permit the five exact archived bodies below and one
bounded DCN index-metadata query to be read into fixture quarantine. This is
a limited exception to event-year acceptance and page-kind enforcement
before historical acquisition, solely to obtain missing real controls for
new-source contracts. It does not accept a year, activate a policy, start V5,
create permanent phase 2 watches, promote production observations or source
generations, or authorize publication. Any additional body needs a separate
reviewed proposal.

The [machine manifest](../../evidence/admission/fixture-exception/v2-new-source-fixture-targets-2026-09-13.json) is the
request allowlist. It includes exact replay URLs, retained CDX rows and file
hashes, conditional query URLs, and the limits below.

## Why an exception is needed

The plan requires enforcement for a historical sheet's page kind before its
first fetch ([implementation plan, lines 41–47](../../../docs/plans/history-and-recovery.md#2-the-ordering-rule))
and excludes fetching history to populate H6's corpus (lines 127–128).
The parser contract requires complete real bodies from the project's archive;
headers alone do not qualify ([parsing, lines 154–165](../../../docs/reference/parsing.md#fixtures-and-tests)).
Each new source kind also needs its own contract and fixtures
([rollout, lines 57–60](../../../docs/plans/recovery/README.md#second-tranche-continuous-repair-and-measured-accuracy)).

SRS has only synthetic parser fixtures plus retained CDX rows and response
headers. DCN has retained index research and response headers, but no complete
HTML or PDF controls in the repository. The existing nine activated page
kinds do not cover either source. This proposal addresses that missing-body
dependency explicitly; it does not treat synthetic passing tests or global
H7 deployment as source-specific admission approval.

## Exact body targets

All timestamps are UTC Wayback capture identifiers. Every original URL and
timestamp pair below occurs verbatim in retained CDX JSON. These are known
fixture candidates, not a claim that they are the best capture for bulk
backfill. Each request uses `https://web.archive.org/web/<timestamp>id_/<url>`.

| ID                 | Capture          | Exact original URL                                               | Intended control                                                                                                                                 |
| ------------------ | ---------------- | ---------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| SRS index          | `20130413022752` | `http://www.steprightsolutions.com:80/events`                    | Series names, cities and per-year event links. Retained response headers confirm this capture, but no body survives.                             |
| SRS event          | `20130401090810` | `http://www.steprightsolutions.com:80/events/asianopen2013`      | Event title/date and ownership of contest-to-round links for the same event as the next two targets. Its exact HTML layout remains unverified.   |
| SRS round 507      | `20160909170855` | `http://steprightsolutions.com/events/asianopen2013/round/507`   | Candidate preliminary control: role-separated bib/name tables, anonymous columns, raw `1/2/3` marks, named panel and promotion styling.          |
| SRS round 508      | `20160909171011` | `http://steprightsolutions.com/events/asianopen2013/round/508`   | Candidate final control: partner names, a printed bib, anonymous per-judge ranks and ordinal placement.                                          |
| DCN event metadata | `20180815191517` | `https://danceconvention.net/eventdirector/en/eventpage/1546230` | One known archived metadata page to inspect its payload format and retain any advertised results/PDF locators locally. No child request follows. |

The SRS [research note](wayback-coverage-2026-09-11.md) and
[capture inventory](../../evidence/collection/wayback-2026-09-11/README.md) describe preliminary
and final shapes across rounds 507/508. Their bodies must confirm the exact
round types. Their existing Memento headers support the listed timestamps.
If these two bodies do not supply both controls, the missing control remains
pending; no other round is fetched automatically. Promotion, alternate
sub-values and finals bib ownership may remain unverified even after reading
them. Preserve that uncertainty rather than supplying an interpretation.

The DCN capture is from 2018. A second retained URL identifies the same event
ID as Riga Summer Swing, but neither its event dates nor the captured page
format have been body-verified. The current playbook describes Nuxt; this
older capture may use a different layout. Do not substitute a guessed
`/results` URL, follow an origin redirect, or assume the metadata page is a
results/PDF fixture.

## DCN index and PDF scope still missing

No exact archived `eventsarchive` capture is retained. Permit one **metadata
query only**, for the known index original URL
`https://danceconvention.net/eventdirector/en/eventsarchive`, restricted to
capture year **2025**, exact URL matching, successful HTML captures and
distinct digests. The machine manifest lists the exact CDX probe and page-0
URLs. Make at most one probe and one page request. If more pages exist, record
the remainder as unexamined. If no capture exists, record that result without
expanding the year, locale, prefix or origin scope.

Returned index capture URLs are local review suggestions; this proposal does
**not** authorize their bodies. A second proposal can name one exact capture
after the metadata is available.

No exact original DCN round-PDF URL or archived PDF timestamp was found in
retained research. `dcn_pdf_head.hdr` records a PDF response but does not
identify its request URL or retain the PDF body. Therefore this proposal
authorizes **zero DCN PDF requests**. Preliminary and final PDF controls,
required by [parsing](../../../docs/reference/parsing.md#parsing-rules), remain a
separate scope. Locators discovered in the permitted metadata body may be
recorded for a future proposal; they are not acquisition permission.

## Hard request and storage limits

- Five exact body targets and at most two CDX requests; **12 HTTP requests
  total**, including archive redirects and any required archive robots read.
- One request in flight, at least ten seconds between archive requests, and
  the project User-Agent. At most three archive-only redirect hops per body;
  the twelve-request total remains the overriding ceiling.
- The requests consume the same persistent archive budget as other work:
  **200 per UTC day**, unchanged. The 2026-09-13 budget is exhausted, so no
  execution is possible on that day's quota. Approval does not reset it.
- At most **32 MiB received total**, **8 MiB per response**, and **15 minutes**
  elapsed. Use the existing 120-second CDX and 30-second body timeouts.
- Zero origin requests, automatic retries, alternate-capture attempts,
  automatic child requests, Save Page Now requests or availability-API calls.
  Stop at a limit or throttle response and retain the unfinished targets.

Five direct bodies plus the probe/page normally require seven requests;
remaining capacity covers a required robots check and some redirects. The
ceiling may prevent completing all targets, which is an explicit incomplete
outcome rather than permission to continue.

## Quarantine and review boundary

Store complete bodies, body hashes, original URLs, actual Memento times,
response headers, request-budget receipts, extracted records and guard
reports in a separate fixture directory. Preserve malformed and incomplete
responses as such. Local discovered locators stay in a review manifest.

Temporary request controls, if required by the fetch client, exist only in
quarantine and become inert after each attempt. The runner must use an
explicit fixture-purpose exception; it must not disguise a round as an
event-list index or set acceptance/deployment flags to bypass checks. Only
shared host-etiquette accounting may change outside quarantine. Production
watches, observations, source-generation selections, canonical facts,
decisions, year acceptance and publication remain untouched.

After capture, compare real outputs and all unknown fields with the synthetic
controls. Add empty, malformed and changed-input regressions, review the
frozen corpus independently, and separately approve each page-kind contract.
The later production read/replay must enter as new historical evidence under
H7; no legacy admission or removal authority is granted by this fixture work.

## Separate 2009 discrepancy

The backfill begins at **2010-01-01**, while WP14's done criterion names all
2009–2016 events ([backfill, lines 7 and 371](../../../docs/reference/backfill.md)). Keep the
2010 boundary until the owner resolves that discrepancy. This proposal names
no 2009 event or round request. The complete SRS index may mention 2009 links;
retaining that untrimmed index neither follows those links nor admits 2009
event facts.
