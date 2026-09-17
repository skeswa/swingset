# Missing historical parser fixtures and unsupported-page meanings

Date: 2026-09-16 UTC  
Type: Investigation  
Topic: DCN, generic score PDFs, and release coverage  
Related: [History and recovery](../../../docs/plans/history-and-recovery.md), [Historical sources](../../../docs/plans/historical-sources.md)

## Finding

Local evidence does not yet justify implementing and admitting a DCN or generic
score-PDF parser. DCN retains discovery metadata and headers, but no complete
results body or score PDF. The retained PDF fixtures are newsletters. This audit
used local files only during schema-23 validation; no source request, source/test
edit, parser activation, or acceptance occurred. The coordinator requested the
prepared fixture exception from the owner; approval was pending when this note
was written.

## Exact fixture gap

[Parsing rules and fixtures](../../../docs/reference/parsing.md#fixtures-and-tests)
require complete real archived bodies. PDF controls must include prelims and
finals. The [DCN contract](../../../docs/reference/sources/danceconvention.md#8-parsing)
describes Nuxt results, older-year Tapestry listings, and score PDFs; those written
shapes do not establish what an archived body actually contains. The
[generic adapter contract](../../../docs/reference/sources/long-tail.md#2-principle)
requires a header vocabulary grown from fixtures and preserves unknown layouts
as unsupported.

Retained DCN inputs are the
[derived event table](../../evidence/collection/source-survey-2026-09-04/aggregators/dcn_events.tsv),
[capture metadata](../../evidence/collection/wayback-2026-09-11/cdx_dcn_eventpage_2013_2018_first50.json),
and response headers. None substitutes for an extractor fixture. There is no DCN
or generic adapter in `sources/`, no corresponding admitted contract, and no
`pdfplumber` dependency. The existing `pypdf` newsletter reader does not establish
score-table extraction. Before eventual registration, reconcile the historical
planner's `dcn.event` name with the documented `dcn.event_results`.

The [exact fixture allowlist](../../evidence/admission/fixture-exception/v2-new-source-fixture-targets-2026-09-13.json)
contains four StepRight bodies, DCN metadata capture `20180815191517` for event
`1546230`, and one bounded 2025 index-metadata query. It authorizes no DCN PDF
body, returned index body, or automatically discovered child. The old metadata
page may have a different format from modern Nuxt. The
[prepared proposal](v2-new-source-fixture-proposal-2026-09-13.md) records these
limits; new bodies need the applicable explicit acquisition authorization.

## Two different unsupported outcomes

[Contract-4 fixtures](../../../src/swingset/admission/fixtures/README.md) deliberately
retain raw EEPro numeric scores and WDR numeric/Solo content. Admission accounts
for these fields; the canonical projector withholds unsupported scoring results.
The retained WDR body contains both supported ordinal rounds and unsupported
contests. Therefore an interpreted page may contain unsupported canonical
content. A page count is not a contest count.

The [coverage contract](../../../docs/reference/data-model.md#event-completion-coverage) defines
counts over one pinned enumeration. Neither a missing adapter, parse failure,
critical unknown, nor an absent published result proves an unsupported whole-page
format. Existing generation result/report JSON, exact accepted decisions, and
verified body/extract support can prove retained canonical limitations. They do
not automatically attribute each observation to each page of an aggregate
manifest.

Keep `unsupported_pages` unassessed until its exact classification is documented.
Do not silently use it for pages containing canonical limitations or subtract
such pages from interpreted totals. A future bounded classifier should share the
projector's pure predicates, pin its format and exact interpretation proof, and
leave ambiguous aggregate ownership unknown. No new output column is proposed
for implementation in this audit.

## Runner readiness

The current reusable helper successfully verified the allowlist's canonical hash
and all five retained evidence/header references after the documentation move.
The manifest remains `c51d9810073d5e49c4a1044e30de97bd4409f3ad4c4a109aa6a2fa31dc51dd93`.

The [retained H13 driver](../../evidence/admission/fixture-exception/fixture-exception-h13-driver-20260913.py)
still imports and pins `research.fixture_exception` and `research.fixture_transport`.
Current helpers live under `journal/tools/admission/`. Its execution check also
requires database schema to equal the exact imported runtime schema. Local
schema 23 cannot be substituted for frozen production schema 14. Prepare a new
reviewed version bound to the chosen helper-inclusive runtime or separately
reviewed helper closure; preserve the retained driver. Do not bypass it by using
the base helper: the [H13 review](../../evidence/admission/fixture-exception/fixture-exception-h13-operations-20260913.md)
already identifies its missing requirement-kind pause and lifecycle handling.
This was static review and manifest verification, not execution-readiness approval.

### Later preparation on the same date

[Driver 002 and its helper closure](../../evidence/admission/fixture-exception-2026-09-16/REVIEW.md)
now address the import/path gap without changing frozen runtime source. All 638
files in the host mirror match the deployed schema-14 receipt. The helper closure
is verified before import, including rejection of undeclared bytecode/extensions
and symlinks. The driver preserves exact schema, publication, H13 control, lock,
and budget checks and requires the scheduled-service hold to remain present.

Local tests passed 63 cases; the coordinator independently reran all 63 and reviewed
the literal changes. The [independent receipt](../../evidence/admission/fixture-exception-2026-09-16/root-review-receipt-001.json)
records engineering preparation acceptance only. The packet and original local
receipt were copied byte-exactly into a fresh evidence directory. No VM staging,
capture, source admission, or owner approval occurred. [D-0038](../../decisions/0038-pin-fixture-helpers-beside-frozen-runtime.md)
records the implementation choice. Actual execution pins and current quota still
require review after owner approval.

## Next step and decision boundary

Acquire only the approved quarantine controls, inspect their actual structures,
then implement the smallest supported parser with real regressions. A speculative
decoded-payload normalizer would not close the current evidence gap. Masked names,
nullable IDs, unknown legends, ambiguous partner ownership, and mixed scoring
methods must retain their documented uncertainty.

The owner decision currently needed is the explicit fixture-acquisition exception.
Module names, conservative classification rules, and source registration naming
are routine implementation decisions to record and review. No additional owner
semantic question is needed merely to leave unsupported-page counts unknown.
Changing an existing public count's meaning would require a documented proposal;
this investigation makes no such change or acceptance claim.
