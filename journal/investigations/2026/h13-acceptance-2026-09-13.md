# Checking pause controls and status reports (H13)

> Historical record. Dates, status claims, and instructions below describe the
> original work. See [current status](../../../docs/status.md) before acting.
> [Project journal](../../README.md)

Pause controls let an operator stop selected work and inspect its status. This record concerns their checks and recovery behavior. The original work ID is H13.

Implemented, deployed and accepted on 2026-09-13 UTC. Production uses the
reviewed schema12 source. No repair kind or
historical year is activated by this revision.

## Admission and draining

Schema12 adds durable control revisions, pause history and execution admissions.
Controls use a restricted connection under a short mutex; they do not wait for
the whole cycle lock or modify interpretation inputs. Requests, derivations,
saved crosschecks, builds and publication use the same dependency gates.
Already admitted work drains; later matching work cannot overtake a waiting
pause. Selective resume leaves other scopes and automatic host cooldowns intact.

Immediate worker transactions have a 45-second wall-clock bound. Python loops
and long SQLite statements are interrupted; nested savepoints inherit the
deadline. Partial output rolls back and a failed derivation retains its token
with `write_deadline_exceeded`. Unchanged failures remain blocked across restart,
while independent work commits. Read snapshots do not hold SQLite's writer
and are excluded from this write budget.

A real two-connection test used a 0.1-second write bound and one-second control
bound. The waiting pause persisted in 0.14007 seconds after the slow unit rolled
back. The next matching admission was denied. The scaled limits make the test
fast; they exercise the same deadline and control code used by the worker.
[Measured test receipt](../../evidence/runtime/h13/h13-control-scenarios-20260913.xml).

## Scenario evidence

| Scenario                                         | Evidence                                                                                                                                                                                                                                                         |
| ------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Pause an active unit with queued work            | The active write drains or rolls back under its deadline; a waiting control wins before the next admission. Request tests keep status `pausing` until the fetched snapshot and body are retained.                                                                |
| Combine scopes and resume selectively            | Source, kind, all and host tests retain overlapping pauses. Independent acquisition and derivation progress. Shared map and source-event work includes all sources and inline repair kinds it actually changes.                                                  |
| Pause a correction or publication                | Valid candidates remain held with visible reasons. Suppression and input validation still run. Control writes commit during a Hub request while semantic writes remain fenced. Lost responses and process death retain uncertainty until receipt reconciliation. |
| Restart or restore with a pause and partial work | Startup recovers admissions before semantic writes. Activated checkpoint tests preserve indefinite pauses, attempts, queue tokens and retry deadlines; admission is denied before resumed work can start.                                                        |
| Inspect paused, stalled and draining scopes      | Doctor and summary expose pause IDs, actor, reason, expiry, exact resume selectors and draining work. Tests distinguish intentional holds, unpaused stalls, stale inventory scans and stuck drains.                                                              |

Source-event aliases use the dependencies of their full mapped event. An unmapped
alias inherits the global mapping operation's dependencies. Map projection also
rebuilds moved events and registry associations; those inline operations cannot
bypass kind pauses. Finding dependency lookup uses indexed watch, snapshot and
subject searches so a long paused queue does not cause a full findings scan for
every unit.

After resume, no-progress clocks subtract the union of recorded operator pause
intervals. Overlaps are counted once and expiry closes its interval. Unknown
legacy starts and timestamps without timezones yield unknown durations; wall
ages remain visible. These clocks use current dependencies and do not reconstruct
past dependency ownership or automatic host cooldown history.

Tests cover CLI parse/project/link/build, ordinary cycles, historical dispatch,
robots requests, redirects, retry accounting, transport exceptions, publication
receipt recovery and actual subprocess death. They use retained fixtures and
fake transports. Final integrated validation passed 896 tests in 27.07 seconds,
plus both subprocess crash/SIGTERM tests in 78.22 seconds. The crash harness now
injects failures after intentional rollback as well as commit. Ruff passed for
runtime and scenario tests; strict mypy passed all 146 runtime modules.
`mise run fmt` completed. The separately reviewed production acceptance helper
has its own 20 passing tests and is outside this runtime freeze.

## Production acceptance

The pinned source is `/nix/store/mn94ln72schglx9fb1qq8k48bv8nl37a-source`;
its 522-file source receipt SHA256 is
`ad95f42e33c551986af2ddaa0199937e4d4ef7bff9f88817f108f412216d0f7e`.
The installed NixOS system is
`/nix/store/l6ac6176giiw4ngwk2044wzcvczjxnpa-nixos-system-swingset-lxc-25.11.20260630.b6018f8`.

Before migration, schema11 was backed up at private commit
`26038f82a082044c4ca04506a27cf380c7e8c476`. All 65,930 checkpoint files were
verified; the manifest SHA256 is
`ab2ddef462bf3248cd65b85cfacbbc589502bee3dc60e3d15174717dd9304cdc`.
Remote privacy, head and manifest were verified at
`2026-09-13T10:08:48.532328+00:00`.

The independently reviewed driver passed preflight and migrated schema11 to12.
Acceptance took 38.649 seconds. Every preexisting table remained unchanged
except schema metadata. Migration installed 171 publication fence triggers;
there were no legacy operator pauses to import. The only added operational rows
were one settled non-network acceptance action and its pause/resume audit events.

The reserved `.invalid` host probe persisted its pause in 0.137902 seconds.
Its entire active window was 0.182497 seconds, within its 30-second watchdog.
Status moved from `pausing` to `paused` after settlement, then `running` after
selective resume. A fresh process read the same paused status at
`2026-09-13T10:18:53.695083+00:00`. A small specimen of those exact control rows
was checkpointed and restored; its pause and active drain survived and
`RESTORE_PENDING` prevented mutation. This was a control-plane specimen,
not a full production restore drill.

After settlement, full doctor output agreed in a fresh process at
`2026-09-13T10:18:53.897370+00:00`. Human and JSON inventories agreed;
read-only reporting changed no protected or control rows. Database integrity
and foreign keys passed. The V4 public baseline remained
`81121dcc7d62a1db9e76ee1c49a6b90e1f4c5653`. No source request or publication
occurred. The filesystem hold remained present and all three scheduled services
and timers remained stopped.

Receipts: [execution](../../evidence/runtime/h13/h13-production-execution.json),
[preflight](../../evidence/runtime/h13/h13-production-preflight.json),
[gate](../../evidence/runtime/h13/h13-production-gate.json),
[backup](../../evidence/runtime/h13/h13-production-backup.json), and
[remote verification](../../evidence/runtime/h13/h13-production-private-verification.json).
Full doctor output and the restored specimen remain under
`/var/lib/swingset/operations/h13-20260913/` on the writer.
