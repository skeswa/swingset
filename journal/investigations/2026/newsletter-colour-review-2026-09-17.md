# Five newsletter colour readings, 2026-09-17

Status: Implemented locally in `wsdc_newsletter.events` parser 8. Validation
and independent review are recorded below. These bytes are outside frozen
runtime 003; they are not deployed, admitted or published. No historical year
is accepted by this review.

Five rows previously inherited the registry fallback even though their
retained PDF specifies a different source label:

| Volume / page | Printed listing            | Printed dates       | Raw type recovered |
| ------------- | -------------------------- | ------------------- | ------------------ |
| 6 / 1         | SWING IN CAPITAL           | Apr 20–22, 2018     | Member Activity    |
| 6 / 1         | GO WEST SWING FEST         | Apr 27–29, 2018     | Member Activity    |
| 9 / 1         | AVIGNON CITY SWING *       | January 18–20, 2019 | Member Activity    |
| 13 / 1        | BY-TOWN ONTARIO OPEN (BTO) | February 7–9, 2020  | Trial Event        |
| 25 / 2        | Swingvasion                | March 10–12, 2023   | Trial Event        |

The [operator evidence and 21-body comparison](../../evidence/collection/newsletter-colour-review-2026-09-17/review-003/review.json)
binds each PDF, snapshot metadata, complete extracted pages and implementation
files. Its retained script reads graphics state at the actual `Tj`/`TJ`
text-show operators, preserving `q`/`Q` scope. It decodes each text object with
the original font resources and byte-string encoding, retaining exact text
matrices and operator offsets. It does not assign colours from the timing of
a later text callback. Pattern colour is unknown. All four reviewed PDFs use
normal blending, opacity one and default filled text for these runs.

Volume 6 prints the member-activity legend and both named rows in DeviceRGB
`0.6 0.6 0.804`. Volume 9 prints its member-activity legend and Avignon in
DeviceGray `0.651`. Volume 13 prints a gray trial legend at `0.651` and the
By-Town row at `0.753`; the printed colour category, title, date and full body
support the reading, not equality to one legend value. Volume 25 prints its
trial legend and Swingvasion in DeviceGray `0.651`.

`colour_review.py` stores only these five dispositions. The body and extracted
pages must both match, and only the exact page/name/start/end key applies.
The parser preserves the raw `Member Activity` label plus its row flag; it
does not convert that label into registry or trial. The existing historical
projector treats this raw label as unknown WSDC status for listing-only events.
Registry-held event status continues through its existing independent evidence.
No schema or global status-model change is made.

Other sidebar rows retain the registry fallback and
`newsletter_status_colour_unverified`. All four bodies retain a colour warning
because this is incomplete row coverage; its evidence records the number of
reviewed rows. The warning retains the prefix used by publication gap reporting.
Real undated, hiatus, cancellation, quarter-only and other warnings remain.

The comparison against frozen runtime 003 parser 7 covers all 21 retained PDFs:
exactly five row labels and their flags change. Every row name, date, location,
row count, legitimate-empty outcome and non-colour warning is preserved. The
remaining 17 PDFs have identical complete parse results. New tests use the
actual four bodies and verify changed body bytes, changed extracted text,
missing/unknown body binding, exact page/name/date scope and unknown legends.
Actual parsed rows also pass through historical projection: member activity
stays unknown, a trial becomes trial, and either listing preserves existing
registry-held status from independent points evidence.
Fresh focused tests and static checks are in the
[checks receipt](../../evidence/collection/newsletter-colour-review-2026-09-17/check-001/checks.json).

[Independent review](../../evidence/collection/newsletter-colour-review-2026-09-17/independent-review-001/checks.json)
passed the same 41 focused tests and Ruff with unchanged source, test, fixture
and evidence hashes. The reviewer reran the retained comparison script against
frozen runtime 003 and reproduced all 21 comparisons and four operator-proof
objects exactly. No blocking finding remains for this local interpretation.

This review made no network request or production operation. It closes five
specific fallback classifications, not the general newsletter-colour research
question. Independent source review and ordinary source-kind policy/admission
checks still apply before runtime use. See [D-0074](../../decisions/0074-review-exact-newsletter-colour-rows.md).
