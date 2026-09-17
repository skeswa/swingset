# Independent fixture audit and parser controls, 2026-09-17

The approved fixture run retained all five exact bodies. Independent offline
review verified all eight response hashes and lengths, exact request allowlist,
canonical authorization and manifest hashes, and target provenance against the
[retained receipt](../../evidence/admission/fixture-exception-2026-09-17/quarantine/receipt.json).
The [machine audit](../../evidence/admission/fixture-review-2026-09-17/audit.json)
pins the receipt and current parser source bytes. No new requests were issued
during this review, and no source, test, helper or retained input was edited.

## Acquisition limits and timing finding

The run used eight HTTP requests: one robots request, five body requests and two
CDX requests. It received 130,059 bytes and lasted 73.255045 seconds. Each body
was complete; the five target requests returned 200. Robots returned 404. No
redirect, origin, PDF, alternate or child request appears in the receipt.
The bounded CDX result reported one page and retained six distinct-digest index
captures. This verifies acquisition, not page-kind admission or publication.

Recorded issue-time gaps were 10.012779, 10.014738, **9.990654**, 10.003321,
10.004283, **9.996796** and 10.004583 seconds. Two fall short of the exact
ten-second minimum by 9.346 and 3.204 milliseconds. Do not round these up or
claim every recorded request interval passed that check.

The frozen transport reserves the next host slot at `Gate.acquire`, then
commits the byte reservation, records `issued_at`, saves the receipt and calls
the synchronous stream. Varying bookkeeping time between grant and issue can
make issue-time gaps shorter than grant-time gaps. Actual socket-dispatch and
response-completion times are not retained, so wire spacing is unknown.
Sequential, synchronous streams provide structural evidence for one request in
flight; these receipt fields alone do not independently measure concurrency.

Before another fixture operation, the maintained transport should enforce a
deadline anchored at dispatch or, more conservatively, the preceding response's
completion. Test variable reservation/receipt-write delays with an advancing
clock, retain the actual boundary times, and keep shared budget and H13 admission
checks. This finding does not justify changing retained evidence or replaying
the completed run. The proposed next index request makes this correction an
explicit prerequisite.

The subsequent read-only export of production request admissions supplies an
[additional audit](../../evidence/admission/fixture-review-2026-09-17/admission-audit.json).
All eight request admissions settled, each before the next admission. Admission
gaps range from 10.001119 to 10.004148 seconds. That verifies serialized admission
and settlement while leaving the two shorter issue-time gaps unchanged; it does
not supply the missing actual dispatch timestamps.

## Step Right controls

The existing index parser produces 22 observations and zero watches from the
real index. It includes printed 2009 rows; parsing retained evidence does not
authorize acquisition below the accepted 2010 floor.

The event capture is dated April 1, before its printed April 25–28, 2013 event.
It has the title and date in an `h2`, an event-site link, and no round links.
The current event extractor rejects it. A later implementation should preserve
this verified metadata-only event shape without claiming a complete round
enumeration, interpreting absent results as a cancellation, or treating arbitrary
empty HTML as a legitimate empty page. The fixture does not control contest to
round-link parsing; a later results-bearing event capture still needs a concrete
fixture proposal if required for admission.

Round 507 is Newcomer Jack & Jill prelims, with ten leaders and fifteen followers.
Round 508 is the matching final with eight couples. Both current round parses
reject the real grouped header as merged or uneven cells. Required fixes are
now concrete:

- Recognize the exact `Judge Scores *` / `Judge Placements *` grouped header
  with `colspan="5"`, expand only that supported judge group and verify each
  data row has the expected width. Arbitrary merged cells remain unsupported.
- Retain the preliminary header's explicit title, `1 = Yes, 2 = Alt, 3 = No`.
  The 125 observed marks contain 71 ones, 13 twos and 41 threes. No alternate
  sub-value occurs. This confirms unranked `2` for this control; it cannot prove
  sub-values never occur elsewhere, and unknown variants must stay findings.
- Keep five anonymous judge columns separate from the five printed panel names.
  The real `.judges` container includes a nested `.judge_note` saying the marks
  are anonymous and chief scores private. The current extract includes this
  note in the roster string. Extract the roster separately while retaining the
  anonymity statement; do not invent a named chief judge from the note.
- Preserve `adv` row classes. Eight leaders and eight followers carry `adv`;
  their names exactly equal the corresponding final participants. This is
  cross-page evidence for promotion in this fixture, stronger than a highlight
  class alone. A bounded reviewed rule must specify when this evidence applies.
- Every final bib/name matches the corresponding leader's preliminary bib/name.
  This supplies leader ownership evidence for all eight observed final rows.
  Do not assign a final bib to both partners, or generalize this cross-page
  verification to unreviewed layouts without an explicit rule.

The comparison used retained source strings; it did not create identities or
canonical observations. Round parsing, empty/malformed/changed-input controls,
independent contract review and page-kind enforcement remain unfinished.

## DCN controls and discovered locators

The 2018 DCN body is a legacy Tapestry metadata page for Riga Summer Swing,
August 9–13, 2018, Riga, Latvia. It contains RequireJS/Tapestry initialization,
no `__NUXT__` payload, no `roundscores` string and no `.pdf` locator. There is
no current DCN source parser to run. This is a real legacy metadata control;
it does not validate current Nuxt results extraction or score-PDF parsing.

The page prints a results-tab link under
`/eventdirector/en/eventpage/1546230-riga-summer-swing/results`, with a captured
`;jsessionid=...` suffix. The session-bearing link remains retained in the raw
body. Removing that routing suffix yields a _candidate normalized locator_,
not an observed archive capture. It was not fetched, and no archive timestamp
for that results tab is established here. No precise preliminary or final PDF
proposal can be made from this body; guessing round identifiers would not fill
that evidence gap.

The separately retained CDX rows support one exact
[DCN index-body proposal](dcn-index-fixture-proposal-2026-09-17.md). That body,
any results-tab metadata lookup, and any PDF fixture remain outside D-0053.
