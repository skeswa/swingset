# Newsletter planning prose, 2026-09-17

Newsletter parser 7 stops reporting reviewed administrative instructions in
five newsletter issues as event approvals. This is implemented locally and passes
22 source tests. It is not deployed, replayed into production, or published.

The fresh production review showed eight prose approval warnings. One was
Volume 23, page 3: existing members should list their prospective dates so new
events can select locations and dates under the council's spacing rules.
The previous parser matched the words “new events” and raised an approval
finding. The sentence names no newly approved edition.

The full PDF was already retained in the
[warning-review bundle](../../evidence/collection/phase1-2026-09-13/warning-triage/).
Its body SHA-256 is
`e2920d5d80beea725d9b36f33ba164cdcc255def99bf73a4b793dbc65db0eeaf`.
The PDF and original snapshot metadata were copied unchanged into the
[newsletter fixture corpus](../../../src/swingset/sources/wsdc_newsletter/fixtures/README.md).
No source request was made.

The change recognizes specific planning, management, application, certification
and posting-schedule phrases verified in the full bodies. It preserves warnings
for other prose, including actual welcome/approval notices and quarter-only
approvals. It does not infer dates, series aliases or trial status, and it does
not make an unknown page an authoritative empty listing.

The [first comparison](../../evidence/collection/newsletter-warning-2026-09-17/comparison-001.json)
checked the initial Volume 23 fix. The coordinator then copied four more
existing full bodies and snapshot metadata from production into
[retained controls](../../evidence/collection/newsletter-warning-2026-09-17/production-controls/),
using read-only state and no source requests. Copies of these controls became
fixtures for Volumes 8, 9, 11 and 29.

The [expanded comparison](../../evidence/collection/newsletter-warning-2026-09-17/comparison-002.json)
ran all 21 full-PDF fixtures against deployed parser 6 and local parser 7.
Every observation and legitimate-empty outcome stayed identical. Eight false
prose warnings were removed across the five issues; no warning was added.
Several warnings can share one code on one source page, so this is not eight
closed production findings. Volume 29 still has its real welcome/approval
notice and an unparsed page warning. Volume 23 still has
39 dated rows and its real undated-listing warning. The original schema-14
source receipt was verified by the
[parallel offline review](../../evidence/collection/phase1-review-2026-09-17/offline-verification.json).

Existing H7 enforcement does not admit this new parser recipe automatically.
Independent interpretation review, policy handling and retained-body replay
remain prerequisites to closing the live warning. Removing this false warning
alone does not make a year ready for owner acceptance.
