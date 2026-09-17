# Exact origin DCN score-PDF controls, 2026-09-17

Status: Scoped under the owner's standing [D-0087 authorization](../../decisions/0087-authorize-remaining-v2-acquisition-and-operations.md).
A reviewed origin-specific runner and fresh execution gates remain required.
No origin request was made during preparation. No further owner permission
question is required for this bounded v2 work.

The [independent metadata audit](../../evidence/admission/dcn-score-pdf-lookup-2026-09-17/independent-review-001/checks.json)
passed. Five requests exposed 156 response-body bytes; all five paid request and
five logical fetch admissions settled. Shared Archive usage rose from 15 to 20
requests and from 2,951,909 to 2,952,065 bytes. Four closed-exchange-to-dispatch
monotonic gaps were 10.015213, 10.016744, 10.023803 and 10.029720 seconds. The
retained coordinator export shows schema 28, the hold, six inactive ordinary
units and unchanged acknowledged publication.

Both exact-URL probes reported one page. Each corresponding page-zero body was
exactly `[]` followed by a newline, SHA-256
`37517e5f3dc66819f61f5a7bb8ace1921282415f10551d2defa5c3eb0985b570`.
These were exact HTTPS URL queries for 2018–2026, HTTP 200, collapsed by digest.
They returned no capture rows; they do not prove the URLs were never archived
or cover another scheme, host, range or query. No alternate query follows here.

The separately audited archived HTML prints these exact links:

- Finals: `https://danceconvention.net/eventdirector/en/roundscores/3451330.pdf`.
- Prelims: `https://danceconvention.net/eventdirector/en/roundscores/3451331.pdf`.

They belong to the selected `Jack'n'Jill Newcomer` contest, ID `2196606`, at Riga
Summer Swing, source event `dcn:1546230`. The finals HTML has nine pair rows;
the preliminary container has an empty leaders table and eight followers rows.
Neither table proves a full participant population or a PDF's actual contents.

The [machine scope](../../evidence/admission/dcn-origin-score-pdf-proposal-2026-09-17/proposal.json)
has SHA-256 `81ebe0dd06c1a748431cb8481f6b4ccfde303d4b5c8e09c1995751218c980bdc`.
It binds the exact HTML, locator context, completed Archive receipts and
independent audit. [D-0088](../../decisions/0088-scope-origin-score-pdf-controls-after-empty-archive-lookups.md)
records the origin operation choice.

The ceiling is **four HTTP requests, two PDF bodies, 2 MiB per response,
8 MiB total and 15 minutes**. The only permitted redirect is one robots hop
from `https://danceconvention.net/robots.txt` to
`https://danceconvention.net/eventdirector/robots.txt`, without query, fragment,
credentials or a host/scheme change. PDFs cannot redirect. There are zero
retries, alternates, children, additional metadata or other origin URLs.

The new runner must use the origin's policy: the project User-Agent, gzip,
matching conditional validators when available, no stored or sent cookies,
Protego rules and at most one robot-policy refresh per 24 hours. A cached policy
needs exact retained-body, host/path, status and freshness proof; an unexplained
304 is not usable evidence. Respect 10-second completion-to-dispatch monotonic
spacing or any stricter rule, one request in flight, actual shared 200-request
and 300 MB daily limits, and at most this one DCN event that day. Account bounded
decoded response-body chunks without slicing or an extra EOF probe at capacity;
keep full reservations on incomplete responses. Wire/decompressor buffering
remains unknown.

The Archive runner cannot simply be renamed: its host, robots handling,
compression and body-plan assumptions differ. Seal an origin-specific helper
closure and independently test exact redirect rejection, gzip/body bounds,
cookie exclusion, conditional representation checks, robots refusal and delays,
H13 controls, midnight accounting, partial completion and source/schema binding.
Keep all retained Archive packets and captured bytes immutable.

One gate needs an explicit coordinator resolution before execution: the
maintained source configuration has no `sources.dcn` entry, and `Config.enabled`
defaults to false. A fixture-only policy must be concretely reviewed and bound
without silently bypassing that state, overriding an explicit kill switch, or
activating an ordinary source kind. Current production source/host policy,
actual origin paid usage and legacy-spacing authority must be checked read-only;
no capacity is reserved by this preparation. Any required stopped-worker host
baseline is a separate reviewed coordinator operation.

The initial reference runtime is deployed source 003/schema 28. Rebind and
review explicitly if deployment changes before execution; do not run against an
unreviewed schema. Retain the hold and six inactive ordinary units, current
public-baseline proof, writer/control serialization, and a fresh single-use
quarantine. The operation writes accounting and operational receipts only.
Actual origin `fetched_at`, `captured_at` and `observed_at` must reflect the new
response time, not the archived 2019 HTML or 2018 event date. Bodies require
independent acquisition audit before offline PDF interpretation. No historical
year, canonical output, source-kind enforcement or publication is accepted.
