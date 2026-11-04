# Run the tests that protect the pipeline

On `postgres-migration`, run `mise run test` for the core suite.
Use `mise run test -- <pytest arguments>` for the commands below.
The warm native Postgres service gives each test a private database, while
pure parser tests start no service. `mise run test-watch -- <selector>`
reruns a focused selection after edits.

The bookmark's default `test` and `check` commands first run the unchanged
30 ms deadline recovery case in one isolated process. After its passing
receipt and cleanup checks, four native Linux clients with four workers each
run the rest of the core. Each client has a 2 GiB RAM-plus-swap limit.
Measured file costs put expensive files first while keeping each file on one
worker. Every phase reports the complete selection; their assignments and
executions must cover it exactly once. The 180-second wall budget includes
both phases, service setup, verification and cleanup; phase totals add work
across workers.
See the [local outcome](../../journal/investigations/2026/stage-1-implementation-2026-11-04.md).

The ordinary core reuses a separate durable physical-test service, prepared
by `mise run dev` and validated on every run. Its fresh-instance database
inventory must match before and after the run; unexpected databases retain
their owner records and block reuse. Native lifecycle fault tests, full runs
and CI keep their fresh physical clusters.

Explicit selectors and worker flags run natively. Focused runs and
`--full-suite` stay serial unless you supply worker flags; for the full
Postgres check use `mise run test -- --full-suite -n 4 --dist=loadfile
--no-loadscope-reorder --max-worker-restart=0`. `SWINGSET_FAST_CORE=0` selects
the native core path for diagnosis. CI runs four native workers inside its
AMD64 test image.

CI's host supplies a separate durable Postgres service for physical-space
and large backup fixtures. They serialize with a session advisory lease
and one 600-second coordination deadline. Waiting clients poll without
holding a transaction snapshot, so they cannot pin a peer's cleanup horizon.
Application deadlines and each case's memory and data assertions stay unchanged.

Run `uv run pytest -q` for the core suite outside that bookmark. It selects the reviewed tests for
data loss, recovery, publication, identity, source parsing and pipeline
integration. That original command is serial. The current development
target is three minutes; bookmark CI uses `swingset.dev.commands check`.

On 2026-09-18, **989 core cases passed in 2 minutes 54 seconds wall time** on
the development Mac; 2,039 extended cases were deselected. The
[validation receipt](../../journal/evidence/runtime/core-test-suite-2026-09-18/receipt.json)
pins the measured code and limits. This is a local test result, not a deployment.
On 2026-09-27, after architecture phase 2, 1,492 core cases passed in 245 s
(1,510 deselected), over the three-minute rule
([maintenance item 9](../plans/README.md)). Phase 2 added seven core files,
122 cases in about 31 s: `test_rwlock.py`, `test_work_queue.py`,
`test_chunked_completion.py`, `test_guards.py`, `test_loops.py`,
`test_acquisition_plan.py` and `test_loop_isolation.py`, which SIGKILLs each
loop inside a write, acquire inside a request and one poison unit's process
through `tests/helpers/loop_fault.py`. The review fixes brought core to
1,537 cases in 284 s on the Mac (2026-09-28); the busy-writer publication
test now asserts the 70 s busy timeout and holds its writers a second each.
Pipeline tests drive the loops through `tests/helpers/loop_ticks.py`: a
round is a plan, an acquire, a derive and a release tick with a mock
transport and a fake clock. The crash file's subprocess
(`tests/helpers/crash_cycle.py`) runs one round per process (phase 5 WP7).

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
publication, or another area listed under the extended suite below. Before the
Postgres port, a full run took about ten minutes and wrote about 15 GiB of temp.
The bookmark's native Postgres full run passed 4,147 cases in 22 minutes
44 seconds and needs about 30 GiB of scratch. Keep the 80 GiB free-disk floor;
see the source-bound checks in the
[local outcome](../../journal/investigations/2026/stage-1-implementation-2026-11-04.md).
The core's 180-second budget is separate.

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
| Loops               | Lock protocol, leases and quarantine, staged generations, guards, and each loop killed mid-write        |

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

Put a new test in the existing file for its area; start a new file only for a
new module. The core run stays under three minutes on the development Mac. If
a new core case pushes it over, move an older exhaustive case to extended
rather than growing the run (D-0175).

Measure with `uv run pytest -q --durations=20` after materially changing the
selection. Use a stable checkout: migration files changed during the initial
timing attempt and invalidated that run. The
[review and measurements](../../journal/investigations/2026/pytest-suite-review-2026-09-18.md)
record the initial full-suite cost and subsequent core validation.
