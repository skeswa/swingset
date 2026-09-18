# Run the tests that protect the pipeline

Run `uv run pytest -q` for the core suite. It selects the reviewed tests for
data loss, recovery, publication, identity, source parsing and pipeline
integration. The goal is fewer than five minutes on the development Mac,
without parallel pytest workers. CI uses this same default command.

On 2026-09-18, **989 core cases passed in 2 minutes 54 seconds wall time** on
the development Mac; 2,039 extended cases were deselected. The
[validation receipt](../../journal/evidence/runtime/core-test-suite-2026-09-18/receipt.json)
pins the measured code and limits. This is a local test result, not a deployment.

Specialist cases remain available in the extended suite. They include historical
operation drivers, broader input matrices, detailed reporting, optimization
variants and exhaustive crash injection. They are maintained tests, not tests
declared safe to delete.

## Choose a run

| Command                                                 | Selection                                         |
| ------------------------------------------------------- | ------------------------------------------------- |
| `uv run pytest -q`                                      | Reviewed core                                     |
| `uv run pytest -q --full-suite`                         | Core and extended, including every crash boundary |
| `uv run pytest -q --full-suite -m extended`             | Extended cases only                               |
| `uv run pytest -q tests/test_fetch.py`                  | Every test in that file                           |
| `uv run pytest -q tests/test_crash_recovery.py -m core` | Focused crash/restart and SIGTERM tests           |

Prefer the core run and the files you changed while iterating. Run
`--full-suite` as a final or important check: before a commit or handoff, or
when a change touches crash recovery, migrations, backup and restore,
publication, or another area listed under the extended suite below. A full run
takes about ten minutes and writes about 15 GiB of temp.

An explicit file, directory or node ID bypasses the default selection. In
particular, explicitly selecting the crash file without `-m core` includes the
exhaustive test. `-k` and `-m` otherwise filter the selected suite; use
`--full-suite` to search all cases by keyword or marker.

The tests are offline. Some extended cases need externally archived evidence
restored to its documented location. The DCN source inventory fixture is also
an external archive member; see the
[archive restoration instructions](../../journal/evidence/README.md#restore-archived-large-files).

## What the core protects

| Area                | Included coverage                                                                                       |
| ------------------- | ------------------------------------------------------------------------------------------------------- |
| Persistent state    | Atomic output, migrations, held roots, retention apply/reclaim, backup and pruning                      |
| Publication         | Acknowledgment, interruption recovery, stale evidence, suppression and identity withdrawal              |
| Source meaning      | Real retained HTML/PDF fixtures, source ownership, malformed payload grammar and projection             |
| Identity            | Confirmed versus tentative joins, paired names, review restrictions and evaluation leakage              |
| Collection          | Actual-host budgets, spacing, pause races, archive/origin permission and restart behavior               |
| End-to-end behavior | Real scoring pipeline, 33-page event completion, restored accounting and scheduling fairness            |
| Crash recovery      | Before/after ten distinct durable transitions, SIGTERM, no unfinished work and verified candidate files |

The crash harness discovers transition ordinals from input acceptance, snapshot
storage, parsing, projection, linking and completed builds. It asserts the
expected transition set instead of hard-coding transaction numbers. The extended
case tests all remaining transaction boundaries, so a full run retains the
exhaustive coverage without repeating the core boundaries.

## Maintain the selection

[tests/suite.toml](../../tests/suite.toml) owns the selection. Whole-file entries
include new cases in that file. A function selector includes all its parameters;
other functions in that file stay extended. An explicit `extended` marker can
keep an exhaustive case out of an otherwise core file.

Add each new test file to core or extended. Default collection fails for an
unclassified file or an unmatched core selector, rather than silently losing
coverage after a file is added or a selected test is renamed. Use an explicit
path while developing a new file, then classify it. Keep core selection based
on the behavior it protects, not simply its duration.

Measure with `uv run pytest -q --durations=20` after materially changing the
selection. Use a stable checkout: migration files changed during the initial
timing attempt and invalidated that run. The
[review and measurements](../../journal/investigations/2026/pytest-suite-review-2026-09-18.md)
record the initial full-suite cost and subsequent core validation.
