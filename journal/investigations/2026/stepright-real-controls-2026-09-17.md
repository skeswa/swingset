# Step Right real-control parser increment, 2026-09-17

Local event and round parsers now read the complete approved fixtures described
in the [independent review](fixture-controls-review-2026-09-17.md). This
increment follows the coordinator's candidate-001 freeze and is not part of
that frozen candidate. It has not been deployed or published, and activates no
source kind. [D-0057](../../decisions/0057-parse-step-right-real-controls-conservatively.md)
records the implementation choice and remaining boundaries.

The grouped judge header expands only for the reviewed preliminary/final
labels, supported legend, bounded group width and matching data rows. Expanded
columns retain the source group label, width and attributes; arbitrary merged
headers remain parse failures. Named roster text excludes nested judge notes,
which remain separate raw evidence and participate in the extract fingerprint.

The metadata-only event body emits its title/date and a `no_round_links`
status with an explicit warning. It requires the actual supported header shape
and matching original event ownership. This is an observation about the body,
not a claim of complete enumeration, absence of results or cancellation.

Four real fixture files are byte-exact copies of the approved quarantine
bodies. A provenance file records each original URL, capture, body hash and
source-receipt hash. Cross-page tests verify all 16 preliminary `adv` names
match final participants and all eight final bibs match leaders. The ordinary
single-page parser still leaves promotion and final bib ownership unknown,
because it does not receive that verified cross-page evidence. Anonymous judge
IDs remain independent of the named panel; plain `2` remains unranked `alt`.

Fresh validation: 35 tests passed across the existing synthetic source tests
and the new real-control test module. Ruff passed for the source package and
new tests; mypy passed for all three source-package files. Tests cover real
metadata, preliminary and final bodies; round trips; incompatible header
widths, labels and legends; merged cells; empty/malformed pages; ownership
mismatch; and changed marks, row classes and judge notes. No network request
was made by implementation or tests. Independent code review and integrated
validation belong to the coordinator's subsequent receipt.

Remaining work includes a real results-bearing event control, the canonical
projection and field-accounting contract, explicit new-kind review/enforcement,
any cross-page promotion/bib rule, and accepted-year historical processing.
The archived preliminary fixture has no alternate sub-values; this does not
prove such values absent across the source.
