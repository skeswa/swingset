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

| Candidate                                                                                             | Schema | Implemented change from the preceding freeze                                                                                                                                                                                                                                                                                                                                                                                                                                       | Tested                                                                                                                                                                        | Deployed                  | Published  |
| ----------------------------------------------------------------------------------------------------- | -----: | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------- | ---------- |
| [001](../../journal/evidence/runtime/event-extension-2026-09-17/validation-001/extension-source.json) |     27 | Initial integrated event-completion extension: event scheduling, progress, accounting, gaps, timing and retirement. Includes disk-backed backup temporary storage.                                                                                                                                                                                                                                                                                                                 | Full run: 2,188 passed, 4 failed. Ruff and mypy passed.                                                                                                                       | No                        | No         |
| [002](../../journal/evidence/runtime/event-extension-2026-09-17/validation-002/extension-source.json) |     28 | Adds durable completion-based request spacing and debit-day preservation; updates Step Right parsing from real controls and incorporates validation fixes.                                                                                                                                                                                                                                                                                                                         | Full run: 2,237 passed, 2 failed.                                                                                                                                             | No                        | No         |
| [003](../../journal/evidence/runtime/event-extension-2026-09-17/validation-003/selection.json)        |     28 | Changes only two scheduler tests to respect the new spacing interlocks. **Runtime identical to 002.**                                                                                                                                                                                                                                                                                                                                                                              | 2,239 passed; Ruff and mypy passed.                                                                                                                                           | Yes, under hold           | No         |
| [004](../../journal/evidence/runtime/event-extension-2026-09-17/validation-004/selection.json)        |     29 | Adds historical dispatch-proof invalidation, selector/currentness performance fixes, DCN index and legacy HTML parsing, and newsletter interpretation work. Adds retained undated-calendar controls.                                                                                                                                                                                                                                                                               | Full run: 2,525 passed, 10 failed. Ruff and mypy passed.                                                                                                                      | No                        | No         |
| [005](../../journal/evidence/runtime/event-extension-2026-09-17/validation-005/selection.json)        |     29 | Corrects the spacing-helper test fixtures and adds explicit schema-29 rejection coverage. **Runtime identical to 004.**                                                                                                                                                                                                                                                                                                                                                            | 2,536 passed; Ruff and mypy passed.                                                                                                                                           | No                        | No         |
| [006](../../journal/evidence/runtime/event-extension-2026-09-17/validation-006/selection.json)        |     29 | Adds the narrowly scoped DCN score-PDF parser and excludes exact manual-registry artifacts from ordinary runtime-recipe invalidation. Also includes new operating tools, tests, documentation and retained evidence.                                                                                                                                                                                                                                                               | 2,648 passed; Ruff and mypy passed; NixOS build passed.                                                                                                                       | Yes, under hold           | No         |
| [007](../../journal/evidence/runtime/candidate-007-2026-09-21/receipt.json)                           |     29 | Candidate 006 plus three build modules: the release closure is proved in a read snapshot before the bounded write, the proof fingerprint gets its own 256 MiB byte cap, and a lost proof is logged. Also restores the scratch-clean timer that candidate 006 predates. **Not built from `main`**, which carries undeployed schema 30 to 32.                                                                                                                                        | 118 focused closure, coverage, completion and write-deadline tests; Ruff and mypy; core suite in the receipt.                                                                 | Yes, 2026-09-21 00:55 UTC | See status |
| [008](../../journal/evidence/runtime/candidate-008-2026-09-21/receipt.json)                           |     29 | Candidate 007 plus: the historical offers pass answers the year gate once per year, stops at the phase deadline and is skipped under backpressure (D-0181); a `swingset accept-year` command (D-0180).                                                                                                                                                                                                                                                                             | 77 focused backfill, rotation, timing and H14 tests; 14 CLI tests; Ruff and mypy; core suite in the receipt.                                                                  | See status                | See status |
| [009](../../journal/evidence/runtime/candidate-009-2026-09-21/receipt.json)                           |     29 | Candidate 008 plus: the offline phase selects 64 units per snapshot, builds are deferred to once per six hours while backpressure is active, and unsupported page kinds are parked for a month (D-0182).                                                                                                                                                                                                                                                                           | 132 focused fairness, selection, manual-snapshot, H14, backfill and CLI tests; Ruff and mypy; core suite in the receipt.                                                      | See status                | See status |
| [010](../../journal/evidence/runtime/candidate-010-2026-09-21/receipt.json)                           |     32 | `main` itself: everything in 007 to 009 plus Step Right admission and projection, the DCN PDF parser, the restore-activation fix, accept-year, the CDX indexing step, the nested-span rule, and schemas 30 to 32 (D-0185). Cycle budget 40 minutes.                                                                                                                                                                                                                                | Full suite 2,436 passed before the last two changes; core suite after them; receipt.                                                                                          | See status                | See status |
| [011](../../journal/evidence/runtime/candidate-011-2026-09-21/receipt.json)                           |     32 | Candidate 010 plus: a build failure no longer aborts the cycle, `series_aliases.csv` rows may carry a month range, and a new `finding_reviews.csv` override records reviewed dismissals (D-0187); the size cap is 32 GB (D-0186).                                                                                                                                                                                                                                                  | Focused history, config, cycle, backfill and H14 suites; core suite in the receipt.                                                                                           | See status                | See status |
| [012](../../journal/evidence/runtime/candidate-012-2026-09-21/receipt.json)                           |     32 | Candidate 011 plus the watchdog unit and alert option, and a repository identity that ignores the path-flake store marker so config-only switches stop re-queuing work (D-0188).                                                                                                                                                                                                                                                                                                   | Cycle and cycle-control suites; core suite in the receipt; the system was built and the watchdog script run once before the switch.                                           | See status                | See status |
| [013](../../journal/evidence/runtime/candidate-013-2026-09-21/receipt.json)                           |     32 | Candidate 012 with publication switched off until identity output is recovered, and a newsletter phase-one finding bounded to its issue year and the next (D-0189).                                                                                                                                                                                                                                                                                                                | Phase-one intake and history inventory tests; core suite in the receipt.                                                                                                      | See status                | See status |
| [014](../../journal/evidence/runtime/candidate-014-2026-09-21/receipt.json)                           |     32 | Candidate 013 plus: the scope writer detaches registry placements before deleting an event the calendar dropped (D-0191); the watchdog also alerts on a low disk quota and on no fetched page for two days, and the worker's `df` prints the quota (D-0190).                                                                                                                                                                                                                       | Projection, history and map-retention tests; reproduced and verified on a scratch copy of the live database; core suite in the receipt.                                       | See status                | See status |
| [015](../../journal/evidence/runtime/v5-year-acceptance-2026-09-22/receipt.json)                      |     32 | Candidate 014 plus: a dismissed parse failure clears its capture from the year gate (D-0193); the size cap is 48 GB (D-0194); the backup unit no longer restarts and refuses to run without disk room (D-0192).                                                                                                                                                                                                                                                                    | Phase-one intake tests; the D-0193 rule was exercised on the live database through the review tool before the switch; core suite not rerun for the config change.             | See status                | See status |
| [016](../../journal/evidence/runtime/v5-year-acceptance-2026-09-22/receipt.json)                      |     32 | Candidate 015 plus: the projection question is answered once per offers pass, dispatch or event-site review, and an allocated dispatch evaluates only its own page (D-0195).                                                                                                                                                                                                                                                                                                       | Backfill, event-site, origin-dispatch and phase-one suites (126 tests); offers pass timed on a scratch copy of the live database; core suite in the receipt.                  | See status                | See status |
| [017](../../journal/evidence/runtime/v5-year-acceptance-2026-09-22/receipt.json)                      |     32 | Candidate 016 with the size cap at 64 GB (D-0196); config only, no runtime change.                                                                                                                                                                                                                                                                                                                                                                                                 | Config and retention-input tests (19); the switch re-queued nothing.                                                                                                          | See status                | See status |
| [018](../../journal/evidence/runtime/stability-rehearsal-2026-09-24/receipt.json)                     |     32 | Candidate 017 plus the stability repairs: an event with a dangling reference is withheld instead of failing the release; the closure proof is retained only after the candidate files, and build recipes name it by digest; build errors set `failed=true`; builds and history are paced (6 h, 12 h); deadline overruns retry with backoff and history gets 300 s; a pending history no longer closes phase two; `gc --closures` collects unnamed proofs (D-0198, D-0199, D-0200). | Full suite 2,467 passed, 0 failed; release build, history and collection rehearsed on a clone of the paused production database; independent review approved with conditions. | See status                | See status |
| [019](../../journal/evidence/runtime/candidate-019-2026-09-25/receipt.json)                           |     32 | Candidate 018 plus: an overrun in a pre-acquisition bookkeeping stage is recorded in `stage_errors` and skipped instead of aborting the cycle; the event-site review answers the projection question once per write (D-0201).                                                                                                                                                                                                                                                      | Full suite 2,470 passed, 0 failed; overrun stack captured on a copy of the production database.                                                                               | See status                | See status |

Candidates 003, 006, 007, 008, 009, 010, 011, 012, 013, 014, 015, 016, 017, 018 and 019 have been deployed in this sequence. Candidate 003's
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
