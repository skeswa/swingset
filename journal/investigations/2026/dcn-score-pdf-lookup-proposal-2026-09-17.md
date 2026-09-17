# Exact Riga score-PDF capture lookup, 2026-09-17

Status: Approved under the owner’s standing v2 authorization in
[D-0087](../../decisions/0087-authorize-remaining-v2-acquisition-and-operations.md).
Execution still requires its reviewed bounded runner and current gates. No
request, runtime change, watch, source-kind activation or publication is claimed here.

The [independently audited Riga results HTML](../../evidence/admission/dcn-results-body-2026-09-17/independent-review-001/checks.json)
prints two score-file links under **Jack'n'Jill Newcomer**:

| Printed round context           | Exact original URL                                                     |
| ------------------------------- | ---------------------------------------------------------------------- |
| Finals                          | `https://danceconvention.net/eventdirector/en/roundscores/3451330.pdf` |
| Prelims — leaders and followers | `https://danceconvention.net/eventdirector/en/roundscores/3451331.pdf` |

The captured page identifies Riga Summer Swing 2018, event `1546230`. Its
canonical URL includes the source-printed `2018` suffix; the two score URLs
are absolute resolutions of printed relative hrefs, with no guessed round ID
or alternate URL. The [retained locator context](../../evidence/admission/dcn-score-pdf-lookup-proposal-2026-09-17/locator-context.json)
binds the HTML body, acquisition receipt and independent audit by hash.
The HTML has nine finals rows, an empty prelim leaders table and eight prelim
followers rows. Those counts do not establish complete participation,
promotion rules, score columns, or the contents of either unacquired PDF.

The [machine proposal](../../evidence/admission/dcn-score-pdf-lookup-proposal-2026-09-17/proposal.json)
asks for one page-count probe and, only when that probe reports captures,
page zero for each exact URL. Its SHA-256 is
`806f9450d2d487fcbb8fc09ec4966cb685eee16099f1dd80c16757ca2b2aa18e`.
Queries use `matchType=exact`, captures 2018–2026, status 200 and digest
collapse. They retain the returned MIME type without filtering it: a printed
`.pdf` suffix does not prove the archived response type. Additional result
pages remain unexamined. An empty result remains a gap.

The ceiling is **five HTTP requests including one robots check**: at most
four CDX metadata requests, 2 MiB per response, 8 MiB total and 15 minutes.
There are **zero PDF, archived-body, origin, redirect, retry or child
requests**. The aggregate ceiling can stop the run before all five requests
if earlier responses consume capacity; it is not a promise to spend five
full response allowances. The byte metric covers bytes exposed by the
streaming reader, not transport buffering. Incomplete responses keep their
reservation.

Preserve one request in flight, at least ten seconds from completed exchange
to next dispatch using the reviewed monotonic check, any stricter active
robots rule, H13 controls and the unchanged 200-request Archive UTC-day
budget. The latest retained export observed 15 paid requests at
17:44:37 UTC, leaving 185 then. This proposal reserves none; the runner must
check actual capacity again at execution.

The previous metadata runner is bound to one exact URL and cannot execute
this proposal unchanged. A new fixed-manifest two-query runner needs
independent review, source/schema/system/baseline checks, the accepted
operator hold, six inactive ordinary units and a fresh separate quarantine.
It may adapt the already reviewed transport and accounting boundaries;
it must not add production acquisition or initialize current inputs.

[D-0069](../../decisions/0069-approve-exact-dcn-results-metadata-lookup.md)
covered the completed results-tab metadata query.
[D-0073](../../decisions/0073-approve-exact-riga-results-html-fixture.md)
approved only the acquired HTML body and explicitly excluded metadata and
PDF requests. The missing decision is approval of this separate exact
metadata proposal. Returned captures would support a later, separately
scoped PDF-body proposal; they do not authorize fetching a PDF.

See [D-0079](../../decisions/0079-propose-exact-riga-score-pdf-metadata.md).
The [offline preparation check](../../evidence/admission/dcn-score-pdf-lookup-proposal-2026-09-17/preparation-checks.json)
verified both printed links and all four exact query URLs with zero network
requests or production operations.
