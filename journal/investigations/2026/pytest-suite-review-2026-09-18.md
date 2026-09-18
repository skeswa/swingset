# Which pytest cases are worth keeping?

Date: 2026-09-18, America/New_York  
Type: Investigation  
Topic: Test suite size and value  
Related decision: [D-0165](../../decisions/0165-reduce-tests-by-behavior-and-tool-lifetime.md)

Follow-up: the owner selected a core default with retained extended tests in
[D-0168](../../decisions/0168-run-core-tests-by-default-and-keep-an-extended-suite.md).
The [testing guide](../../../docs/guides/testing.md) describes the implemented
commands. The initial assessment and full-suite timing below remain historical.

## Summary

The suite collects **2,998 cases across 217 files**, from 1,944 test functions.
Getting below 1,000 means removing at least 1,999 cases, about two thirds.
This review does **not** establish that those cases are dispensable.

Three subagents reviewed operational tooling, integrity, and source meaning;
the primary agent reviewed scheduling and combined the findings. The first
plausible reductions total **36 cases**. Retiring eleven historical operational
drivers could save another **243 net cases**, after preserving reusable safety
checks. That leaves an estimated **2,719 maintained cases**. Selecting another
190 tool tests separately would leave **2,529 in the routine run**, but would not
reduce the maintained total.

These are conditional estimates, not an implemented or validated reduced suite.
No tests, runtime code, fixtures or discovery settings changed. The
[file-by-file inventory](pytest-suite-inventory-2026-09-18.md) covers all 217 files.

## What is key

Keep distinct observable failures, particularly these groups:

| Contract                        | Representative tests                                                                                                       | Why it matters                                                                                                                     |
| ------------------------------- | -------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------- |
| Durable state and safe removal  | `test_retention_apply.py`, `test_derivation_payloads.py`, `backup/test_checkpoint.py`, `test_crash_recovery.py`            | Different crash points leave different persisted state. Protect references, held roots, atomic output, locks and restart recovery. |
| Publication and identity safety | `publish/test_recovery.py`, `publish/test_safety.py`, `test_correction_only.py`, `test_h16_acceptance.py`                  | A local build is not a remote acknowledgment. Revoked identities, stale evidence and uncertain publication must not escape.        |
| Real source meaning             | `test_dcn_legacy_results.py`, `test_dcn_score_pdf.py`, `test_steprightsolutions_real_controls.py`, `test_real_fixtures.py` | Preserve distinct layouts, known source defects, printed values and provenance.                                                    |
| Identity boundaries             | `test_identity_decisions.py`, `test_identity_evaluation.py`, `test_link_service.py`                                        | Similar names are not confirmed identities; paired names, source-ID conflicts and evaluation leakage need separate checks.         |
| Collection controls             | `test_request_spacing.py`, `test_fetch_controls.py`, `test_history_shared_gates.py`, `test_history_origin_dispatch.py`     | Recheck permission during waits, robots and redirects; retain actual-host accounting and spacing after restart.                    |
| Completion and restored work    | `test_event_completion_cohort.py`, `test_event_accounting_restore.py`, `test_event_extension_acceptance.py`                | Prove real progress through the pipeline and restored-versus-uninterrupted behavior.                                               |
| Bounded selection and evidence  | `test_selector_currentness_cache.py`, `test_link_dependency_selection_scale.py`, `build/test_closure_validation_scope.py`  | Avoid stale cached authority, full-catalog scans and false completion from partial verification.                                   |

The single 33-page completion-cohort test is especially valuable: it drives
real fetch, parse and admission while competing work arrives. Conversely,
`test_crash_recovery.py` has only two collected items but includes many process
crash scenarios. Collection count alone does not measure cost or breadth.

## First reduction candidates: 36 cases

Paths below are under `tests/`. These candidates need focused validation after
editing. Current branch equivalence is not proof against every future mutation.

| Test or group                                                                                         |                   Change | Savings | Required replacement or surviving check                                                                                                                                                                        |
| ----------------------------------------------------------------------------------------------------- | -----------------------: | ------: | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `test_identity_corrections.py::test_unrestricted_division_is_unavailable_at_every_dancer_level`       |                   21 → 9 |      12 | The implementation returns unavailable before using dancer level. Keep all seven levels with division `None`, plus `none` and `open` with novice. Keep real audit and printed-ID abstention cases.             |
| `test_link_pools.py::test_resolution_preserves_full_pool_candidate_signals`                           |                  42 → 30 |      12 | Remove only extra source-ID variants for empty, punctuation-only and paired names. Keep both subject kinds and a nonempty printed ID; explicitly assert no candidates. Keep every meaningful-name combination. |
| Paused-cycle wrappers in `test_event_accounting.py`, `test_event_gaps.py`, `test_event_retirement.py` | 4 → 1 including the base |       3 | Each calls `test_event_progress.py::test_paused_cycle_does_not_verify_artifacts_or_record_observed_progress` with identical fixtures. Move all extra empty-table assertions into that base scenario.           |
| `test_extension_migration_receipts.py::test_protected_rows_cannot_change_behind_new_schema`           |                    3 → 1 |       2 | The synthetic ordinary tables have identical schemas and behavior. Keep one plus the separate schema-metadata exception test.                                                                                  |
| `publish/test_huggingface_publish.py::test_initial_head_rejects_every_other_file`                     |                    3 → 1 |       2 | Keep one foreign file rejected by the generic allowlist, plus accepted metadata and exact README tests. This deliberately stops sampling two other foreign names.                                              |
| `test_artifact_recovery.py::test_exact_artifact_restored_from_valid_private_checkpoint`               |                    6 → 5 |       1 | Drop only corrupt-extract: wrong-content-extract reaches the same digest rejection. Keep missing extract and all three body-damage cases.                                                                      |
| `test_jes_test.py::test_preliminary_only_does_not_agree_with_numeric_final_place`                     |                    1 → 0 |       1 | The stronger `test_preliminary_participation_is_covered_but_agreement_is_unknown` constructs the same final evidence and also asserts absence of a final result.                                               |
| `test_stepright_body_runner.py::test_preflight_report_explicitly_withholds_readiness_and_execution`   |                    1 → 0 |       1 | Transfer exact report/hash assertions to `test_sealed_cli_preflight_checks_full_boundary_without_network_or_vm`, which already verifies the operational boundary.                                              |
| `test_project_steprightsolutions.py::test_projector_version_covers_step_right_projection`             |                    1 → 0 |       1 | Only checks `PROJECTOR_VERSION == 20`. Keep behavioral projection and actual version-change repair tests; preserve a rollout pin elsewhere if still required.                                                  |
| `test_retention_plan.py::test_checkpoint_tests_still_pass_with_the_moved_closure`                     |                    1 → 0 |       1 | Checks aliases and absence of old symbols. Keep behavioral checkpoint/GC tests; first confirm module identity is not an intended public contract.                                                              |
| **Total**                                                                                             |                          |  **36** | **2,998 → 2,962**                                                                                                                                                                                              |

The primary agent checked the reduction examples against their test bodies.
A second reviewer confirmed the paused-cycle fixtures are identical. Another
cross-reviewed the identity matrix reductions. Do not hide parameter cases
inside loops or combine unrelated scenarios merely to reduce the reported count.

## Larger opportunity: retire completed operational drivers

These eleven suites contain 268 cases. Their executables pin completed historical
schema, source, checkpoint or release identities. The current
[status](../../../docs/status.md) and
[tool index](../../tools/admission/README.md) support considering them for
retirement; they do not establish that retirement is already accepted.

| File suffix after `tests/test_`     | Current cases | Reserved reusable checks | Potential net saving |
| ----------------------------------- | ------------: | -----------------------: | -------------------: |
| `dcn_origin_fixture_runner.py`      |            37 |                        3 |                   34 |
| `event_extension_live_migration.py` |            26 |                        3 |                   23 |
| `extension28_checkpoint_helper.py`  |            27 |                        0 |                   27 |
| `h11_operational_preparation.py`    |            33 |                        7 |                   26 |
| `h12_operational_preparation.py`    |            17 |                        1 |                   16 |
| `h13_operational_preparation.py`    |            20 |                        3 |                   17 |
| `h14_operational_preparation.py`    |            17 |                        2 |                   15 |
| `h15_production_acceptance.py`      |            21 |                        4 |                   17 |
| `h16_production_acceptance.py`      |            24 |                        0 |                   24 |
| `v4_candidate_acceptance.py`        |            16 |                        0 |                   16 |
| `wp16_operational_preparation.py`   |            30 |                        2 |                   28 |
| **Total**                           |       **268** |                   **25** |              **243** |

The 25 checks are a planning reserve, not a completed replacement design. Map
rollback, receipt recovery, preserved control/budget state, source verification,
checkpoint closure, bounded transport and resume semantics to current tests
before retiring any file. Reuse existing coverage where it proves the same
behavior. Retire executable entry points and update current instructions
together; preserve historical evidence unchanged.

Some dependencies prevent simple deletion. The documented derivation replay
tool imports helpers from `accept_h11`; successor tests also import its test
loader. Fixture packet builders reuse earlier preparers. H14 and H15 have
identical-looking tests that call different copied implementations. Extract
needed helpers or retire their callers first. Runtime files such as
`test_h15_acceptance.py` and `test_h16_acceptance.py` are **not** in this list.

## Separate selection: 190 cases, zero deletions

These tool-specific suites can be selected explicitly if their dependency
triggers are maintained. They remain required when their tool or dependencies
change and before execution. Keep them in the current suite until that selection
exists; use periodic full runs to catch missed dependencies.

| File suffix after `tests/test_`       |   Cases |
| ------------------------------------- | ------: |
| `replay_phase1_newsletter_parser8.py` |      61 |
| `measure_state_storage.py`            |      31 |
| `export_phase1_year_review.py`        |      27 |
| `legacy_spacing_baselines.py`         |      18 |
| `current_schema29_rehearsals.py`      |      15 |
| `fixture_exception_preparation.py`    |      13 |
| `schema29_overhead.py`                |      10 |
| `extension_restore_rehearsal.py`      |       6 |
| `offline_selector_profile.py`         |       4 |
| `research_checkpoint_readers.py`      |       3 |
| `retained_phase1_review.py`           |       2 |
| **Total**                             | **190** |

For example, the 61-case newsletter replay suite protects a fixed 28-target
operation and its 185 non-target ledger records. It is not redundant with the
product parser. The new measurement suite protects read-only checkpoint use
and must accompany changes to schema, backup and retention dependencies.
Active migration, input-acceptance and unfinished-intake checks remain in the
conservative routine estimate even where they also exercise journal tools.

## Conclusion and follow-up

First implement the 36 small candidates in focused batches. Then decide which
old executables remain supported and perform retirement with coverage transfers.
Neither stage credibly gets this suite below 1,000. Even the estimated reduced
total is 1,720 above the maximum of 999.

A sub-1,000 **smoke selection** is a different goal: it can give fast feedback
while the full suite remains a release gate. A sub-1,000 **maintained suite**
would require broader consolidation of duplicated implementations, retirement
of supported behavior, or an explicit acceptance of less regression coverage.
This review has not designed or validated either sub-1,000 option. The proposed
[decision](../../decisions/0165-reduce-tests-by-behavior-and-tool-lifetime.md)
records that distinction without treating it as owner acceptance.

## Evidence and reproduction

Baseline: working change `ttnwxvylxqvqzkrulqwqtyonkwnlppry`, observed commit
`97040e8fe907abe8131034b8a29ff426674b512e`, parent `12a7ffa0`. This is the existing
uncommitted checkout, including new retention work, not deployed candidate 006.

```sh
.venv/bin/python -m pytest --collect-only -q
```

Collection completed without errors: **2,998 cases in 1.12 seconds**. All four
review assignments were checked to cover the 217 files exactly once, and their
counts reconcile to the collection. All scoped test names, assertions and
parameter lists were examined; deeper production-source review concentrated
on candidate reductions and critical boundaries. This is not exhaustive proof
that every retained test is minimal.

The unchanged identity candidate files (`test_identity_corrections.py`,
`test_link_pools.py`, `test_project_steprightsolutions.py`) passed **85 tests in
1.53 seconds**. The four unchanged paused-cycle nodes passed **4 in 1.55 seconds**.
The full suite, proposed replacements, mutation tests and a runtime profile were
not run. Existing passes establish a baseline, not reduced-suite equivalence.

SHA-256 of sorted collected node IDs, each followed by a newline:
`dea23db477f553be19d40c65d1d841a7d29171e84694f38e8a852b9a468491c9`.
SHA-256 of sorted lines containing each test file path, one space, its content
SHA-256 and a newline:
`ab74de905687c9077130eb1546b2fb811a9325c5b3761f5a48c874b35245a619`.
Disposable collection output and working review notes stayed in scratch storage;
the retained inventory records every file, count and disposition.

## Follow-up: measured runtime on 2026-09-18

The owner then asked how long the suite takes. A serial run on this ARM64 Mac,
using the existing Python 3.12.13 environment, took **637.27 seconds wall time
(10 minutes 37 seconds)**. Pytest reported **2,965 passed, 33 failed**, no setup
errors, and two warnings; its own measured duration was 636.84 seconds.

| Work                                                                   |     Measured time |
| ---------------------------------------------------------------------- | ----------------: |
| Exhaustive transaction crash/restart test                              |    264.19 seconds |
| 33-page completion under competing work                                |     18.27 seconds |
| Archived ScoringDance end-to-end pipeline                              |      7.07 seconds |
| Everything except the exhaustive crash test, including runner overhead | About 373 seconds |

The single exhaustive test accounts for about **41%** of wall time. A baseline
probe of its helper reported 91 transaction boundaries: testing both sides of
each boundary and restarting each crashed process requires 364 child runs,
plus the initial baseline. This test passed in the measured run. Its count of
one collected case hides most of the work. The timing supports investigating
that test's execution cost before expecting large speedups from trimming many
small parameter cases; no test-selection change was made.

JUnit case totals put the 190 proposed separately selected tool tests at only
**7.93 seconds combined** in this run. The median case took **0.08 seconds**,
including setup and teardown; 2,973 of 2,998 cases took under one second.
These figures include the failed cases and should not be read as clean-suite
speedup guarantees.

The first attempt ran in the shared checkout and took 424.36 seconds, but
migration edits occurred during execution. Loaded Python code disagreed with
the SQL files on disk, causing a missing `derivation_rows_legacy` table and
widespread failures. That run is not a useful suite benchmark.

The second run used an isolated Jujutsu workspace at frozen source commit
`cf00288e6b38bd7534266f4d12e781f83fb23557`. `PYTHONPATH` selected that workspace's
source and tests; the existing venv supplied Python and dependencies. The
command was `python -m pytest -q --durations=25 --junitxml=<scratch-report>`,
wrapped in `/usr/bin/time -p`. A single helper probe ran alongside it for about
0.7 seconds. No parallel pytest workers were used.

This is **not a clean passing-suite timing**. Of its 33 failures, 26 reported
missing fixture or retained-evidence files in the fresh workspace, six were
schema/migration assertions or errors, and one rejected an evidence packet's
extra files. These were not repaired for this measurement. The initial review
and this newer snapshot also differ because development continued between
them. Raw logs and JUnit output stayed in disposable scratch storage. The
passing tests establish the main runtime hotspots; a fully passing timing
still requires a coherent checkout with its complete fixture environment.

## Implemented core selection and passing timing

The owner next requested a high-impact default below five minutes and explicitly
chose to keep an opt-in extended suite. [D-0168](../../decisions/0168-run-core-tests-by-default-and-keep-an-extended-suite.md)
records that accepted scope. The initial retirement estimates above were not
used as a deletion list.

The final default **passed 989 cases in 173.73 seconds of pytest time, 174.38
seconds wall time (2 minutes 54 seconds)** on the same ARM64 Mac with Python
3.12.13. The run was serial, with 2,039 extended cases deselected. Full discovery
collects 3,028 cases. The two warnings concern existing `record_property` calls
and pytest's default JUnit format, not test failures. An earlier iteration of
the core also passed 988 cases in 181.11 seconds wall time.

The default covers backup and pruning, retention and migrations, publication
safety, real parser fixtures, identity rules, controls, restored state, and
selected pipeline/closure boundaries. Historical tools, detailed reporting and
broader parameter matrices stay extended. Named function selections retain all
their parameters. New test files and renamed core selectors require an explicit
selection update. Seven independent pytest integration tests cover that policy,
including explicit paths, opt-in full selection and cached `--lf` reruns.

The crash harness now identifies ten durable transitions from their callers:
accepted inputs, a saved snapshot, completed parsing, six projection/link
transitions and a completed build. Each gets its own before/after subprocess
crash case. With SIGTERM recovery, the 21 core crash cases took **32.85 seconds**
including setup/teardown in the final integrated run. Recovery also checks no
unfinished derived work, twelve event fields, a completed build receipt and
candidate file hashes. The extended case exercises the other 81 transaction
boundaries. No runtime fault-injection hooks were added.

The largest other retained case was real completion of a 33-page event amid
competing work, at 21.21 seconds call time. The real scoring pipeline took
7.69 seconds. Both remain core because they prove useful end-to-end behavior.

Validation used an isolated copy of source revision
`72ead87cd3a087348c16323722ad3b2bbe21c0cd`, with the final selection-hook changes
identified by content hashes in the
[compact receipt](../../evidence/runtime/core-test-suite-2026-09-18/receipt.json).
The documented archive restore verified and restored all thirteen external
files before execution, including the real DCN index fixture. Runtime source
and final edited tests were compared with the shared checkout. Formatting used
`mise run fmt`; targeted Ruff checks passed. This supersedes the earlier failed
snapshot as evidence for the **core** runtime, not for a passing full suite.

The full extended suite was not rerun for this selection change. The modified
extended crash case was tested separately; its result is in the receipt.
The [testing guide](../../../docs/guides/testing.md) owns current commands and
[tests/suite.toml](../../../tests/suite.toml) owns the exact selection. All verbose
iteration logs remain disposable; the receipt retains the final counts, timings,
code hashes, command and validation limits.
