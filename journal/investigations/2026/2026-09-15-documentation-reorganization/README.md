# Reorganizing the documentation for new readers

> Follow-up: [D-0005](../../../decisions/0005-research-under-journal.md) moves
> the remaining tools and evidence into the journal. The original path-preservation
> choice below is superseded; retained file bytes are still preserved.

Date: 2026-09-15  
Type: Outcome  
Topic: Documentation  
Decision: [D-0001](../../../decisions/0001-documentation-and-decisions.md)

## Summary

Separate current explanations and instructions from historical investigations
and decisions. Give readers a short path from purpose to pipeline to detailed
rules. Keep source evidence and reproduction scripts at their existing paths.

## Findings

The review counted 103 Markdown files and roughly 172,000 words in `design/`,
`docs/`, and the top level of `research/`, excluding verification and workflow
output. The design reading order listed 29 documents. Current rules, pending
plans, and operating receipts overlapped.

One concrete conflict was the old decision log's allowance for probable public
person IDs. The identity reference and publication code required confirmed
matches. The legacy record is preserved with a visible correction and a link
to [D-0002](../../../decisions/0002-confirmed-public-identities.md).

## Changes

- Add a short overview, pipeline example, task guides, and code map.
- Keep exact rules in reference pages, grouped by reader question.
- Split the migration plan, recovery contracts, and competition-rule chronology
  into summary pages with linked details.
- Keep current status in one page, linked to dated evidence.
- Move research narratives and earlier implementation records into the journal.
- Add formal decision IDs, acceptance records, replacement rules, and templates.

Historical findings keep their original scope. Importing an old choice does not
invent an acceptance date. Captured files keep their original bytes, including
old paths that identify the checkout used to produce them.

## Validation

`mise run fmt` completed. Ruff passed for the seven Python files whose
references or output path changed. The affected research-helper tests passed:
36 cases across `test_h11_operational_preparation.py` and
`test_research_checkpoint_readers.py`.

The real `swingset enums --write` command wrote `docs/reference/enums.md` with
content identical to the old generated page. A local file and heading-link
check passed, and every documentation page has an incoming navigation link.
SHA-256 checks confirmed that all 706 retained verification, workflow-output,
and fixture files were byte-for-byte unchanged. No full pipeline test or
production recheck was needed for this documentation change.

## Evidence and navigation

- [Documentation entry point](../../../../docs/README.md)
- [Writing rules](../../../../docs/writing.md)
- [Old-to-new document paths](paths.md)

No production activity, source requests, commit, or push is part of this reorganization.
