# D-0170: Pin TMPDIR in the dev shell so test temp is pruned

Recorded: 2026-09-18  
Decided by: agent  
Topic: Development environment  
Supersedes: —  
Superseded by: —

## Decision

The dev shell's `shellHook` removes nix's fresh `/tmp/nix-shell.XXXXXX`
while it is still empty and then exports `TMPDIR`, `TEMPDIR`, `TMP`, `TEMP`
and `NIX_BUILD_TOP` as `/tmp`. `nix develop` otherwise points those at the
fresh directory and deletes it on exit only if it is still empty and `TMPDIR`
still names it, so any command that writes there leaks the whole directory,
and overriding `TMPDIR` alone leaks an empty one (verified: six shells with the
final hook, three of them writing, left no directory behind). With a stable
`TMPDIR`, pytest's own pruning keeps only its last three `pytest-N` trees, and
uv's lock file lands in `/tmp`. The
crash-recovery test also removes each `before-N` and `after-N` state copy as
soon as it has been verified. The guides say that `--full-suite` writes about
15 GiB of temp per run and that the worker's `/tmp` is a 13 GiB tmpfs, so a
full run there needs `--basetemp` under `/var/tmp`.

## Why

On 2026-09-18 the Mac reached 98% disk use: `/private/tmp` held about 470 GiB
in 1,201 leaked `nix-shell.XXXXXX` directories, 34 of them full pytest runs at
about 15 GiB each, one every 10 to 15 minutes from agent sessions running
`nix develop -c uv run pytest`. Verified directly: a `nix develop -c` command
that writes nothing is cleaned up; one that writes a single file leaks on
normal exit, `SIGTERM` and `SIGKILL` alike. macOS's periodic `/tmp` cleanup
had hidden the smaller leaks. See the
[investigation](../investigations/2026/nix-develop-tmpdir-leak-2026-09-18.md).

## Alternatives

- `--basetemp=.pytest_tmp` in `pyproject.toml`. Rejected as the only fix: it
  wipes every run's temp, which loses the last runs' `tmp_path` for debugging,
  and it does nothing for uv or other writers of `TMPDIR`.
- Rely on the core suite being the default (D-0168). Kept, but insufficient:
  any `--full-suite` or explicit-path run would still leak.

## Consequences

Test temp now accumulates in `/tmp/pytest-of-<user>/` and is pruned to three
runs; on macOS the periodic cleaner removes the rest. Anyone who needs a run's
temp longer copies it out. The `TMPDIR` override applies to everything run in
the dev shell.

## Links

- [Investigation](../investigations/2026/nix-develop-tmpdir-leak-2026-09-18.md)
- [Development guide](../../docs/guides/development.md)
