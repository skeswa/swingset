# Runtime candidate history

This page tracks what changed in each frozen application-source candidate for
the 2026-09-17 event-extension work. Use it to compare implementations and their
validation results. [Current status](../status.md) owns the live operating state;
[the operating handoff](../../journal/investigations/2026/event-extension-operating-handoff-2026-09-17.md)
holds exact deployment pins.

A numbered source candidate such as **005** is different from a published dataset
candidate such as `cand_8f31cad7226643ae`. It is also separate from the
[SQLite schema version](schema-history.md), a checkpoint number or a rehearsal
packet number. A new source candidate can change only tests while keeping the
runtime and database schema identical.

## Changes and outcomes

Evidence checked on 2026-09-17, through candidate 006's schema-29 deployment and
production input acceptance. **Implemented** means the change is present in that frozen source.
**Tested** describes validation of those exact bytes. **Deployed** means the
runtime was activated in production. **Published** means a subsequent dataset
release was acknowledged; a source build or scratch replay does not establish it.

| Candidate                                                                                             | Schema | Implemented change from the preceding freeze                                                                                                                                                                         | Tested                                                   | Deployed        | Published |
| ----------------------------------------------------------------------------------------------------- | -----: | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------- | --------------- | --------- |
| [001](../../journal/evidence/runtime/event-extension-2026-09-17/validation-001/extension-source.json) |     27 | Initial integrated event-completion extension: event scheduling, progress, accounting, gaps, timing and retirement. Includes disk-backed backup temporary storage.                                                   | Full run: 2,188 passed, 4 failed. Ruff and mypy passed.  | No              | No        |
| [002](../../journal/evidence/runtime/event-extension-2026-09-17/validation-002/extension-source.json) |     28 | Adds durable completion-based request spacing and debit-day preservation; updates Step Right parsing from real controls and incorporates validation fixes.                                                           | Full run: 2,237 passed, 2 failed.                        | No              | No        |
| [003](../../journal/evidence/runtime/event-extension-2026-09-17/validation-003/selection.json)        |     28 | Changes only two scheduler tests to respect the new spacing interlocks. **Runtime identical to 002.**                                                                                                                | 2,239 passed; Ruff and mypy passed.                      | Yes, under hold | No        |
| [004](../../journal/evidence/runtime/event-extension-2026-09-17/validation-004/selection.json)        |     29 | Adds historical dispatch-proof invalidation, selector/currentness performance fixes, DCN index and legacy HTML parsing, and newsletter interpretation work. Adds retained undated-calendar controls.                 | Full run: 2,525 passed, 10 failed. Ruff and mypy passed. | No              | No        |
| [005](../../journal/evidence/runtime/event-extension-2026-09-17/validation-005/selection.json)        |     29 | Corrects the spacing-helper test fixtures and adds explicit schema-29 rejection coverage. **Runtime identical to 004.**                                                                                              | 2,536 passed; Ruff and mypy passed.                      | No              | No        |
| [006](../../journal/evidence/runtime/event-extension-2026-09-17/validation-006/selection.json)        |     29 | Adds the narrowly scoped DCN score-PDF parser and excludes exact manual-registry artifacts from ordinary runtime-recipe invalidation. Also includes new operating tools, tests, documentation and retained evidence. | 2,648 passed; Ruff and mypy passed; NixOS build passed.  | Yes, under hold | No        |

Candidates 003 and 006 have been deployed in this sequence. Candidate 003's
[schema-14-to-28 live migration](../../journal/evidence/runtime/event-extension-2026-09-17/live-migration-001/migration-receipt.json)
preserved all 71 predecessor application tables. Candidate 006 then preserved
all 116 schema-28 tables while adding schema 29, and its separately sealed
production input acceptance passed under hold. Ordinary collection resumption
remains a separate gate. The published dataset
remains the earlier H16 release, commit
`2a6c7dc744fb36eabb5163c0a527d787d3721f4f`; none of these extension candidates has
produced an acknowledged new publication.

## Why the failed candidates were replaced

**001:** Two subprocess tests imported the changing checkout instead of the
frozen runtime. A corruption test tried to use SQLite `blobopen` on an indexed
column, and a coverage test had an outdated assertion. The later validation
procedure bound subprocesses to the freeze and corrected those tests. The original failed full
run remains [retained](../../journal/evidence/runtime/event-extension-2026-09-17/validation-001/pytest.log);
separate focused passes do not turn it into a passing full run.

**002 → 003:** Both failures expected requests that the new spacing contract
correctly refused: an immediate post-crash request and a request under an older
schema without spacing state. Candidate 003 changes only
`tests/test_event_extension_acceptance.py` and `tests/test_event_service.py`.
The [selection receipt](../../journal/evidence/runtime/event-extension-2026-09-17/validation-003/selection.json)
confirms every other file is identical. The
[failed 002 run](../../journal/evidence/runtime/event-extension-2026-09-17/validation-002/pytest.log)
and [passing 003 validation](../../journal/evidence/runtime/event-extension-2026-09-17/validation-003/validation.json)
remain distinct evidence.

**004 → 005:** Ten spacing-helper tests used the new default schema 29, while
the operation helper deliberately permits only schemas 14 and 28. Candidate
005 changes only `tests/test_legacy_spacing_baselines.py`, pins the intended
schema and tests rejection of schema 29. The helper's runtime behavior did not
change. Keep the [failed 004 validation](../../journal/evidence/runtime/event-extension-2026-09-17/validation-004/validation.json)
separate from the [passing 005 validation](../../journal/evidence/runtime/event-extension-2026-09-17/validation-005/validation.json).

## Candidate 006's runtime differences

Exactly five runtime files differ from 005:

- `src/swingset/sources/dcn/score_pdf.py` is new. It parses only the two reviewed
  Riga PDF layouts and retains source/page provenance, printed panels, marks,
  results and population limits.
- `src/swingset/sources/dcn/records.py` adds typed PDF observations;
  `adapter.py` and `__init__.py` register and expose the offline parser.
- `src/swingset/state/work.py` keeps the exact manual `registry_crosscheck`
  artifact on its dedicated replay path when the runtime recipe changes.
  Ordinary and unknown parser snapshots still invalidate normally.

The [PDF interpretation record](../../journal/investigations/2026/dcn-score-pdf-parser-2026-09-17.md)
and [manual routing record](../../journal/investigations/2026/manual-registry-replay-routing-2026-09-17.md)
explain their limits. The PDF change enables no ordinary source kind, canonical
projection or historical watch. The routing change does not delete an existing
stale queue entry. Neither change accepts a historical year or supplies H17
human labels.

The [006 selection receipt](../../journal/evidence/runtime/event-extension-2026-09-17/validation-006/selection.json)
lists all changed and removed paths, including non-runtime files. Its
[full validation](../../journal/evidence/runtime/event-extension-2026-09-17/validation-006/validation.json)
verified all 2,939 frozen files before and after testing; mypy checked 225 source
files. Later working-copy edits are outside that receipt.

The exact candidate-006 system was activated under the operator hold. Its
[schema-29 migration receipt](../../journal/evidence/runtime/held-schema29-migration-2026-09-17/execution-001/receipt.json)
and [independent audit](../../journal/evidence/runtime/held-schema29-migration-2026-09-17/postmigration-review-001/receipt.json)
verify the deployed source and system, all 116 unchanged predecessor tables and
the sole new dispatch-fence table. This deployment did not accept production
inputs, resume collection, activate repairs or publish a dataset release.

## Following the evidence

Each `validation-NNN/extension-source.json` contains a path-to-SHA-256 inventory.
Compare two inventories for the exact file changes; the summary above explains
their purpose. A `selection.json`, where present, records deliberate inclusions,
exclusions or comparison with a predecessor. Whole-freeze file counts include
code, tests, fixtures, documentation and retained evidence: a larger count does
not mean the runtime grew by that many files.

A freeze-time `deployed: false` field records the state when that receipt was
created. A later deployment receipt can supersede that status, as it did for
003; it does not rewrite the old receipt.

Build, service-binding, migration, restore and replay receipts have separate
source pins. For example, candidate 005's successful schema-29 rehearsals do not
validate candidate 006. A failed operational attempt also does not necessarily
create a new source candidate. See the
[continuation journal](../../journal/investigations/2026/v2-continuation-2026-09-17.md)
and [successor rehearsals](../../journal/investigations/2026/schema29-successor-rehearsals-2026-09-17.md)
for dated operation outcomes.

Add a row when a new source is frozen. Record its predecessor, substantive
changes and any test-only boundary; link its inventory and exact validation.
Update deployment or publication only from the corresponding completed receipt,
and preserve earlier failed attempts.
