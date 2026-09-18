# D-0099: Parse DCN score PDFs as scoped source observations

Recorded: 2026-09-17  
Decided by: agent  
Topic: DCN PDF interpretation  
Supersedes: —  
Superseded by: —

## Decision

Implement a bounded offline `dcn.round_pdf` parser from the independently audited
Riga finals and preliminary bodies. Preserve printed headings, page-local judge
codes and names, marks, results, numbered finals columns and remarks. Keep
preliminary roles separate and finals names as a printed pair. Retain body/page
provenance and reject unsupported layouts or ambiguous field boundaries.

Use the printed preliminary legend for callback marks and retain any supplied
alternate rank. Printed results remain source observations. Do not infer the
numbered finals columns' formula, assign a pair bib to either person, merge
cross-page identities or treat omitted participants as absent from the contest.

Keep archived HTML and current PDF observations separate. Their name difference
at finals bib 485 is a source disagreement, not permission to choose an identity.
No ordinary-kind enforcement, canonical projection, historical watches or year
acceptance follows from adding this parser.

## Why

The two acquired bodies now supply real controls for the legacy PDF layout.
Their preliminary disclaimer explicitly describes a filtered population, and
the retained HTML covers different slices and an older observation time. A
source-scoped parser can preserve useful evidence while those limits remain
visible. Acquisition and parser tests do not prove whole-event completeness or
identity precision.

## Links

- [Standing acquisition authority](0087-authorize-remaining-v2-acquisition-and-operations.md)
- [Acquisition outcome](../investigations/2026/dcn-origin-runner-2026-09-17.md)
- [Independent PDF controls](../investigations/2026/dcn-score-pdf-controls-2026-09-17.md)
- [DCN source contract](../../docs/reference/sources/danceconvention.md)
