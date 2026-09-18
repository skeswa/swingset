# Disk leak from `nix develop` and pytest

Date: 2026-09-18. Purpose: explain why `/private/tmp` filled the Mac and what
now prevents it. Decision: [D-0170](../../decisions/0170-pin-tmpdir-in-the-dev-shell-so-test-temp-is-pruned.md).
Diagnosis by the owner's session; fix and verification by an agent.

## Symptom

The Mac reached 98% disk use with 22 GiB free of 926. `/private/tmp` held
about 470 GiB across 1,201 `nix-shell.XXXXXX` directories, 1,038 of them made
that day. 34 were full swingset pytest runs at about 15 GiB each; the rest were
small (a uv lock file, sometimes a partial pytest tree). Removing them
reclaimed about 420 GiB.

## Cause

`nix develop` creates `/private/tmp/nix-shell.XXXXXX`, exports it as `TMPDIR`
for the command, and on exit removes it with a non-recursive delete, which
succeeds only if the directory is still empty. Verified directly: a
`nix develop -c` that writes nothing is cleaned up; one that writes a single
file leaks on normal exit, `SIGTERM` and `SIGKILL` alike. It is not a crash or
timeout problem.

- Every `nix develop -c uv run ...` leaked, because uv writes `uv-<hash>.lock`
  into `$TMPDIR` on start; 843 leaked directories carried swingset's lock hash.
- pytest writes `$TMPDIR/pytest-of-<user>/pytest-0`. Its keep-last-three
  pruning never ran because every run had a brand-new `TMPDIR`.
- Until the core suite became the default (D-0168), plain `pytest` ran the
  extended suite. `test_restart_before_and_after_remaining_cycle_transactions`
  in `tests/test_crash_recovery.py` leaves about 90 `before-N` and `after-N`
  state copies at about 21 MiB each, and a full run totals about 15 GiB across
  about 3,900 test directories and 287,000 files.
- A 15 GiB run landed every 10 to 15 minutes from 02:36 to 13:33 as agent
  sessions repeatedly ran `nix develop --command uv run pytest`.
- macOS's periodic `/tmp` cleanup absorbed the older, smaller leaks (nothing
  older than Sep 14 survived), which hid the problem until full-suite runs
  started.

## Fix

The dev shell's hook removes nix's directory while it is still empty and then
exports `TMPDIR`, `TEMPDIR`, `TMP`, `TEMP` and `NIX_BUILD_TOP` as `/tmp`, so
pytest prunes to its last three runs and uv's lock lands in `/tmp`. A first
attempt that only exported `TMPDIR=/tmp` still left an empty `nix-shell.*`
behind on every shell, because nix's exit cleanup removes the directory only
while `TMPDIR` still names it. With the final hook, six `nix develop -c`
shells, three of them writing a file into `TMPDIR`, left no directory behind,
and Python's `tempfile.gettempdir()` reports `/tmp`. The crash-recovery
test removes each state copy once it is verified. The development guide and
AGENTS.md state the cost of `--full-suite` and the worker's tmpfs limit.
