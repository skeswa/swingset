# Stage 0 implementation, 2026-11-04

Stage 0 is implemented locally on `postgres-migration`. Production SQLite
was not opened, deployed or re-derived. Gate 0 remains open: Postgres core
and full tests, sampled-import verification and a zero-backlog re-key belong
to the stage 1 port. No dataset was published.

Three subagents owned recipes and audit acceptance, graph verification,
and re-keying. The session integrated the CLI and release guard. The re-key
agent reviewed the verification and guard integration.

## What changed

- Captured transform declarations determine code recipe values; build keeps
  its closure digest. Exact closure/runtime digests use the audit consumer.
- Verification freezes its expected units, clones a checkpoint per process,
  compares real output and dependencies, and writes a receipt. Missing,
  duplicate, failed and interrupted work cannot authorize release or re-key.
- Release waits for verification of changed scopes, or verified declared
  bumps with all dependent work drained. Initial empty states establish
  an audit baseline; populated legacy imports require verification.
- Re-keying walks dependencies, batches equivalences and resumes without
  duplicate rows. Bumped producers and unresolved consumers remain queued.

## Validation

- `nix develop -c uv run pytest -q`: **2,179 passed**, 1,546 deselected,
  727.39 seconds. This ran alongside exhaustive recovery; it exceeds the
  three-minute core budget. Feedback time remains a limit tracked by
  maintenance item 9 and the stage 1 development-loop work.
- `nix develop -c uv run pytest -q --full-suite -m extended`: 1,532 passed
  initially; 13 failures came from obsolete invalidation fixtures and code
  changing between crash subprocesses. The affected files were rechecked:
  **114 passed**. The separate exhaustive recovery rerun passed all 81
  remaining transaction boundaries, before and after interruption, in
  611.38 seconds. There are no unresolved test failures; this is combined
  coverage with focused reruns, not a single clean full-suite invocation.
- Updated fixture groups: **74 loop/isolation tests** and **33 release/history
  tests** passed. Recipe, verification and re-key tests also passed.
- `mise run fmt`, `nix develop -c uv run ruff check .` and
  `nix develop -c uv run mypy src/swingset` passed.

The review approved the local change after fixing incomplete receipt coverage,
retired scopes, and version-bump repair evidence. Missing runtime scopes fail
closed. Legacy repairs without declared-version evidence need another clean
verify after draining. Production validation and Gate 0 are still pending.

Logs remain in local scratch under `/tmp/swingset-stage0-*`; no production
evidence was made.

The verification fixtures include the real 33-page EEPro cohort, composite
source replay and a stored projection/link graph. An injected extra read
changes the dependency comparison even when output stays byte-identical.
The CLI test verifies a checkpoint and re-keys the import using its receipt.

[Plan and gate](../../../docs/plans/postgres-migration/5-stage-0.md).

## Follow-up, 2026-11-05

Stage 1 closed Gate 0 on the bounded PostgreSQL import: all 6,498 graph units
verified clean and re-key left every unit current. The complete regression
suite and empty-restore release proof also passed. Production still runs
SQLite. See the [stage 1 outcome](stage-1-implementation-2026-11-04.md)
for the final source-bound checks and sample limits. The November 4 results
above remain the record of what had passed at that time.
