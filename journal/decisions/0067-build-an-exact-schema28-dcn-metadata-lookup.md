# D-0067: Build an exact schema-28 DCN metadata lookup

Status: Proposed  
Recorded: 2026-09-17  
Accepted: —  
Acceptance source: —  
Topic: Bounded DCN results-locator metadata runner  
Supersedes: —  
Superseded by: —

## Decision

Build a separate, fixed-manifest runner for the one source-printed DCN results
locator. Bind its runtime to reviewed source 003 and schema 28. Reuse that
runtime's fixture helpers and explicitly adapt a copy of the retained H13
wrapper. Keep all retained packets and runtime files unchanged.

The packet permits only Archive robots, the exact CDX page-count probe, and
page zero when that probe reports pages. It permits at most three HTTP requests,
no redirects or retries, 2 MiB per response, 4 MiB in total, and 15 minutes. It
cannot request an archived result body, PDF, origin page or additional CDX page.
Specific owner authorization remains a separate record; this implementation
choice grants no acquisition permission.

## Why

The prior fixtures expose a results-tab locator but no actual results or PDF
capture. An exact metadata lookup can identify the next reviewable capture
without guessing a URL or granting permission for its body. A distinct packet
makes this narrow authority independently auditable.

Schema 28 requires a monotonic clock for durable host spacing. The old wrapper's
temporary clock exposed only wall time and would restart spacing recovery on
each attempt. The new wrapper keeps the actual clock and records the request day
from the ordinary grant's durable debit timestamp. Tests cross midnight between
debit and transport dispatch.

## Consequences

Preparation verifies the exact source, packet, schema and acknowledged baseline
under the existing writer lock. It retains the service hold and records actual
budget/control observations. Execution rechecks H13 controls and ordinary host
accounting. Unknown legacy spacing remains blocked for the coordinator's
separately reviewed baseline operation; this packet does not initialize it.

The new transport records its handoff and closed-exchange timestamps, including
monotonic elapsed time. It waits a full initial gap and preserves the completion
floor across variable bookkeeping and wall-clock changes. Recorded handoff is
not claimed to be a packet-level wire timestamp.

Response-body accounting uses the existing fetching contract's application
reader boundary; it does not measure wire buffering. Round available byte
capacity down to a 64 KiB multiple, count all delivered chunks without slicing,
and stop at the cap without an extra EOF read. Keep an incomplete response's
full byte reservation and report observed body bytes separately. These rules
reduce usable capacity when necessary; they do not increase any approved limit.

All output goes to a fresh, separate single-use quarantine. The limited SQL
writer permits request accounting and H13 lifecycle records, not observations,
watches, input acceptance or admission activation. Returned metadata still
needs independent review. Any later results or PDF request needs its own exact
proposal and owner decision.

## Links

- [Exact metadata proposal](../investigations/2026/dcn-results-locator-proposal-2026-09-17.md)
- [Runner preparation and checks](../investigations/2026/dcn-results-lookup-runner-2026-09-17.md)
- [Fetching contract](../../docs/reference/fetching.md)
