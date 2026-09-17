# DCN Riga score PDF controls (2026-09-17)

This review interprets the two exact score PDFs linked by the retained Riga results HTML. It establishes visible PDF layouts and their limits for a later offline parser. It does not approve a source kind, identify people across sources, accept a historical year, or claim complete contest populations.

## Evidence and method

The inputs are the original source-printed locators under the selected `Jack'n'Jill Newcomer` contest in retained HTML SHA-256 `487b38c2ff264e62bf2e6de8e2b24cd5aff2ceab40856a56c1ac8539a4aad573`, plus the corresponding origin-captured PDF bodies. The acquisition receipt is `journal/evidence/admission/dcn-origin-runner-2026-09-17/quarantine-001/receipt.json`; it records both exact requested URLs, final URLs, status 200, body lengths and hashes. This interpretation review is separate from the origin-runner acquisition review.

The files were opened locally with pypdf 6.18.1 in strict mode. The reproducible extractor is [extract_controls.py](../../evidence/admission/dcn-score-pdf-controls-2026-09-17/extract_controls.py), and its generated page text, page dimensions and input hashes are [extracted.json](../../evidence/admission/dcn-score-pdf-controls-2026-09-17/extracted.json). No network access was used. The one-page finals PDF is landscape; the two-page prelims PDF is portrait. Text extraction exposes the relevant tables and disclaimers. A later coordinator visual check rendered and inspected all three pages, confirming the columns, row alignment, blank finals remarks and printed disclaimer. The [rendered-page evidence](../../evidence/admission/dcn-score-pdf-visual-review-2026-09-17/receipt.json) is separate from this text extraction; neither check establishes general parser acceptance.

The HTML prints `/eventdirector/en/roundscores/3451330.pdf` in the Finals container and `/eventdirector/en/roundscores/3451331.pdf` in the Prelims container. Both the Prelims leaders and followers HTML tables share the latter URL. Those are the only URL bindings assessed here.

## Printed PDF content

The finals page is headed `Jack'n'Jill Newcomer - Finals` and `Riga Summer Swing`. It names seven judges by two-letter codes: OD, SK, DK, OM, MM, MS and HT. Its legend reads `Score legend: placement from 1 to 9`. The table prints bib, a two-line pair name, one placement under each of the seven judge codes, nine additional columns headed `1-1` through `1-9`, `Result`, and `Remarks`. It has nine bib rows and the Result values 1 through 9. Some numbered columns contain a count with a parenthetical number, such as `4 (6)`; other cells are dashes. The PDF does not explain how the numbered columns, counts, parenthetical values, or remarks are calculated. A parser must preserve those printed values and column headings verbatim unless a separate source contract explains them; it must not derive a formula or promote a bib to either named person's identity.

The prelims file has separate pages headed `Jack'n'Jill Newcomer - Prelims - Leaders` and `... - Followers`. Each has a role-specific single-name table. Leaders list judges SK, MM, LT, AV and CHB; followers list OM, ATP, MS, HT and CHB. The printed legend is `1 = YES, 2 = ALT, 3 = NO; additional ranking may be provided for ALT`. Values include `1`, `2.1`, `2.2` and `3`; the Result column contains `Callback`, `Alternate1`, or `-`. The leaders page has nine rows, all marked Callback. The followers page has sixteen rows: nine Callback, one Alternate1 and six `-` results. Both pages print this disclaimer: `Dancers without any Yes or Alternate marks are omitted from this list. You may check your invididual results on your danceConvention.net account page.` The omission rule makes these sheets an explicitly filtered population; the PDFs do not give a total entrant count.

The two role pages must remain separate. The shared judge code CHB is printed as Chuck Brown on both pages; judge-code/name mappings are page-local source facts, not a global judge identity registry. The prelims use bibs different from the finals for many of the same printed names, so bib is round-local evidence and cannot be used alone as a cross-round person key.

## HTML comparison and limits

The retained HTML Finals table has nine rows. Eight pair names match the PDF's two printed names for the same bib. For bib 485, the PDF prints leader `Lilio Montel` with `Julija Losane`, while the HTML Finals table prints `Lucien Blaise` with `Julija Losane`. This is a real source disagreement in the retained bodies. Keep both raw observations, attach a conflict finding to the bib/round comparison, and do not choose a name or infer whether this reflects a correction, rendering issue, or different entrant.

The retained HTML Prelims leaders table has a header but zero rows, although the PDF leaders page prints nine callback rows. The HTML Prelims followers table has eight rows (placements 10 through 17): seven names/bibs also occur on the PDF followers page, while the HTML's bib 487 / `Vivian Morad` row does not occur in that PDF page. The PDF page includes nine callback rows plus the lower rows; the HTML table's eight rows are not a substitute for that filtered PDF sheet. This difference is not evidence that either source is exhaustive or wrong: record separate round/role observations and report unmatched rows without filling gaps.

The HTML table supplies placements (including intervals), not judge marks. Do not translate PDF callback, alternate, or elimination marks into an HTML placement or vice versa. The PDF's own disclaimer and the limited HTML rows do not support a complete-population inference.

## Conservative parser requirements

A later `dcn.round_pdf` parser should fail closed on unrecognized headings, missing/duplicate judge columns, unexpected page counts, broken row pairing, ambiguous bibs, or malformed mark cells. It should emit the round and role from the exact source-printed heading; retain raw judge codes/names, each raw mark, each raw result, each raw finals column and the printed Remarks cell; and preserve the PDF body hash and page number with every observation. Pair names on finals remain a source-printed pair until individual ownership is independently supported. Preliminary leaders and followers remain separate single-person rows, with no inferred identity links to finals. Preserve `-` distinctly from an absent row, and preserve placement intervals as intervals. Do not convert the disclaimer into an invented count or add omitted dancers.

Cross-source checks may compare only the selected contest's actual HTML locators and source-printed bib/name rows. A disagreement such as bib 485 must remain unresolved. Do not use this control to train or imply human identity labels, judge adjudication, source-kind admission, production watch creation, ordinary acquisition, or year acceptance.

## Status

The two bodies are captured in quarantine and independently interpreted here. Their text and row controls are **inspected**, not implemented as a parser, tested as parser behavior, deployed, or published as dataset facts. `dcn.round_pdf` remains outside ordinary source admission. No year is accepted by this review.
