# Exact DCN results-capture lookup, 2026-09-17

Status: Approved by the owner on 2026-09-17; see [D-0069](../../decisions/0069-approve-exact-dcn-results-metadata-lookup.md).
Independent runner review and coordinator preparation passed. Execution
finished at 17:17:13 UTC using three HTTP requests and 369 accounted response
body bytes. One capture row was returned; the
[independent acquisition audit](../../evidence/admission/dcn-results-lookup-2026-09-17/independent-review-001/checks.json)
passed. No results body or PDF was fetched.
[Retained execution evidence](../../evidence/admission/dcn-results-lookup-2026-09-17/).

The next missing control is an actual results page that can identify score-file
locators. The retained `cdx_dcn_eventpage_2013_2018_first50.json` has five data
rows: the already captured Riga metadata URL, its `info` tab, and two other
numeric event URLs (one with two captures). It supplies no results-tab or PDF
capture. The broader pre-2018 retained CDX file has 26 asset URLs and also no
results/PDF control. The current index body supplies 50 event-page locators but
no PDF link. None proves results or PDF coverage unavailable; that coverage
remains unassessed.

Propose only the exact metadata lookup for this source-printed results tab:

```
https://danceconvention.net/eventdirector/en/eventpage/1546230-riga-summer-swing/results
```

The retained legacy body prints this path with a trailing archived
`;jsessionid=...` routing suffix. The query removes only that suffix, as the
offline metadata parser does; it does not invent an event, round ID or tab.
The [machine proposal](../../evidence/admission/dcn-results-locator-proposal-2026-09-17/proposal.json)
contains the complete printed href, body hash, exact CDX URLs and retained
capture-inventory hashes.

Use CDX `matchType=exact`, captures 2018–2026, status 200 and HTML, collapsed by
digest. One page-count probe may be followed by page zero only if the probe
reports results. Record any further pages as unexamined. There are no prefix
or wildcard searches, extra aliases, alternate queries, retries or redirects.

The ceiling is three HTTP requests including robots, at most two metadata
requests, 2 MiB per response, 4 MiB total and 15 minutes. There are **zero body,
PDF, origin or child requests**. Preserve one request in flight, the reviewed
completion-plus-monotonic gap of at least ten seconds, any stricter active rule,
and the unchanged shared 200-request UTC-day Archive budget. Observe actual
remaining capacity at execution; this proposal reserves none.

[D-0053](../../decisions/0053-approve-exact-new-source-fixture-exception.md)
covered a different two-query metadata lookup, already consumed.
[D-0060](../../decisions/0060-approve-exact-dcn-index-fixture.md)
permitted zero CDX requests. Neither authorizes this new lookup. Execution also
requires a newly reviewed fixed-manifest driver bound to the then-current
runtime/schema and baseline, fresh separate quarantine and coordinator
serialization through existing H13, hold, host and robots gates.

Returned capture rows are review material only. A results body requires a
subsequent exact body proposal; any PDF control first needs an actual printed
locator and capture evidence. An empty lookup stays an explicit gap, without
probing guessed PDF IDs. No watch, observation, historical-year acceptance,
source-kind activation or publication follows from this query.
