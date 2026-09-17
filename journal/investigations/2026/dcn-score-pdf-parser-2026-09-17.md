# DCN Riga score PDF parser, 2026-09-17

The local `dcn.round_pdf` parser preserves the printed scores from the two
reviewed Riga PDFs. It is implemented and passed 13 focused tests, Ruff and
mypy. [Independent review](../../evidence/admission/dcn-score-pdf-2026-09-17/independent-review-001/independent-review-receipt.json)
also passed on the corrected exact bytes. Its bytes are outside frozen candidate
005; this increment is not deployed or published.

## Scope and evidence

The exact fixtures are the independently audited
[origin acquisition](dcn-origin-runner-2026-09-17.md). The
[controls review](dcn-score-pdf-controls-2026-09-17.md) and separate rendered-page
inspection establish the fields in these three pages. The parser accepts only
the exact two reviewed round URLs and source event `dcn:1546230`.

Finals retain nine printed pairs, their row bibs, seven judge columns, nine
opaque numbered columns, printed results and blank remarks. Names remain an
ordered pair without individual role or bib ownership. Preliminary leader and
follower pages retain separate panels and rows, each raw mark, its printed
legend interpretation, alternate subrank and printed result. Their disclaimer
makes the population a filtered subset. No omitted entrant count is inferred.

Each page observation retains the source URL, body hash, page number, headings,
judge codes and names, column names, raw row text and full extracted page text.
Registered observation payloads round-trip through the existing serializer.
The archived HTML and current PDF disagree at finals bib 485; tests preserve
both source facts without selecting an identity or adding a human label.

## Rejection and resource limits

The parser rejects foreign contexts, wrong round/page combinations, changed
headings or column sets, ambiguous judge panels, repeated bibs, malformed
marks, broken pair rows and unsupported results. It rejects unaccounted text
between a score legend and the table header. Coordinator review reproduced
that previously ignored text, and the implementer added finals and preliminary
mutation regressions before independent review.

Extraction allows at most 256 KiB of PDF input and two pages, with at most
16,000 extracted characters per page and 512 per line. Parsing bounds rows and
names. The page-text checks occur after pypdf decoding; they are not a hard
pre-decompression memory or CPU limit. This narrow fixture implementation does
not establish safe general PDF admission.

## Remaining gates

The adapter is registered only within the offline DCN source package. Ordinary
source-kind admission, canonical projection, watches, year acceptance and
complete-event claims remain pending. A future observation conflict check must
retain the HTML/PDF discrepancy when these facts enter canonical processing.
No source configuration or admission policy was enabled by this work.

See [D-0099](../../decisions/0099-parse-dcn-score-pdfs-as-scoped-source-observations.md),
the [parser](../../../src/swingset/sources/dcn/score_pdf.py),
[tests](../../../tests/test_dcn_score_pdf.py) and
[source contract](../../../docs/reference/sources/danceconvention.md).
