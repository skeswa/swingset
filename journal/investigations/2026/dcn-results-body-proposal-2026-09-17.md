# Exact archived DCN results body, 2026-09-17

Status: Approved by the owner on 2026-09-17 in [D-0073](../../decisions/0073-approve-exact-riga-results-html-fixture.md).
The exact capture completed at 17:43 UTC with two requests and 16,839 accounted
response-body bytes. [Independent acquisition review](../../evidence/admission/dcn-results-body-2026-09-17/independent-review-001/checks.json)
passed. Interpretation and page-kind admission remain separate.

The completed metadata lookup identifies one HTML capture of the Riga Summer
Swing results tab. Its [independent acquisition audit](../../evidence/admission/dcn-results-lookup-2026-09-17/independent-review-001/checks.json)
passed: three settled requests, 369 accounted response body bytes, exact
allowlisted URLs and verified retained hashes. The two completion-to-dispatch
intervals were 10.020956 and 10.024278 seconds. The paid Archive ledger moved
from 10 requests / 2,934,701 bytes to 13 requests / 2,935,070 bytes. The retained
coordinator export records schema 28, the hold, six inactive ordinary units and
the unchanged acknowledged public baseline. This review made no live request.

Propose one exact archived body:

```
https://web.archive.org/web/20190719204919id_/https://danceconvention.net/eventdirector/en/eventpage/1546230-riga-summer-swing/results
```

The [machine proposal](../../evidence/admission/dcn-results-body-proposal-2026-09-17/proposal.json)
has SHA-256 `17dea52f53b41b04093f4582a94da0f58afbe808408304f9681e2f3750575483`.
It binds the exact CDX row and its independently reviewed response body. The
row reports timestamp `20190719204919`, HTML, digest
`4EU7SEVHMTLTWY2WZZWXHJHUBZOO2I7T` and length `4805`. That length is Archive
metadata, not a measured body size. The page contents and any score-file links
remain unknown.

The ceiling is **two HTTP requests including robots, one archived HTML body,
2 MiB per response, 4 MiB total and 15 minutes**. This retains the established
bounded receiver limits instead of treating CDX length as a reliable transport
bound. There are zero metadata, redirect, retry, alternate-capture, PDF, origin
or child requests. A printed link can inform a later exact proposal; it grants
no request here.

D-0073 supplies specific owner approval. Execution still needs an independently
reviewed fixed driver bound to the actual runtime, schema and public baseline. Use a fresh
single-use quarantine and current host/robots, H13 and shared budget checks.
Preserve the hold and coordinator serialization, one request in flight, at
least ten seconds from completion to dispatch using monotonic time, any
stricter active rule and the unchanged 200-request UTC-day Archive limit.
The byte bound measures the response body exposed by the streaming consumer;
wire buffering is unknown. Incomplete responses retain their full reservation.

[D-0069](../../decisions/0069-approve-exact-dcn-results-metadata-lookup.md)
states: “Any returned body locator needs a separate exact acquisition
proposal.” Its metadata approval does not cover this body. D-0073 supplies that separate approval. Acquisition
review and offline parser review follow the reviewed runner execution. This
proposal accepts no year, page kind, interpretation, watch or publication.
