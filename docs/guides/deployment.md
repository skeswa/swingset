# Installing the NixOS worker

This guide describes the existing OrbStack setup. Read [current status](../status.md)
and preserve any operating hold. Local development steps are in the
[development guide](development.md). The PostgreSQL/Dokploy target has a
[separate plan](../plans/postgres-migration/README.md).

## Development and VM setup

```sh
orb create --cpus 4 --memory 8G nixos:25.11 swingset
orb config set machine.swingset.disk_bytes 274877906944
orb -m swingset
cd /Users/skeswa/repos/skeswa/swingset
sudo nixos-rebuild switch --flake .#orb --impure
systemctl list-timers 'swingset-*'
journalctl -u swingset-cycle -f      # with the loops on: journalctl -u 'swingset-*' -f
```

The machine is bounded to 256 GiB of disk (`disk_bytes`), and the restore
machine to 64 GiB. Without a bound OrbStack lets a machine grow until the Mac
itself is full, which stops every write on the Mac, not only the worker. On
2026-09-18 the unbounded machine reached 402 GB, almost all of it rehearsal
copies under `/var/tmp` and old checkpoints, and the Mac ran out of space.
OrbStack enforces the bound at once as a btrfs quota on the machine's
subvolume; `df` inside the machine still shows the whole shared image, so read
usage against the bound from `orb info swingset` on the Mac. Inside the bound, the
`swingset-scratch-clean` timer removes idle rehearsal scratch and the backup
command prunes old timer checkpoints; see the
[operation guide](operation.md#disk-space).

A switch that migrates the state database may leave the file larger than its
contents: a migration that drops a table, such as schema 32, frees pages to
SQLite's free list without shrinking the file. Run `swingset gc --reclaim` after
such a switch, as the [operation guide](operation.md#disk-space) says, or the
old size stays on disk and in every backup.

## Switching to the loops

`services.swingset.loops.enable` (default false) replaces `swingset-cycle` with
five oneshot services and timers
([D-0215](../../journal/decisions/0215-loops-share-the-state-under-a-reader-writer-lock.md),
[scheduling: loops](../reference/scheduling.md#loops)). A switch that turns it
on removes the cycle's service and timer, and systemd stops them; turning it
off does the reverse. `dryRun` picks `--dry-run` or `--publish` for release.
Turn it on in a switch of its own, with `src/` unchanged, after the same
package has migrated to schema 34 and drained in cycle mode
([phase 2 deploy steps](../plans/architecture.md#9-migration-plan)). A loop
never migrates while others run: a tick that finds an older schema tries one
exclusive open, which migrates, or skips.

| Unit                | Timer                            | Budget option (default)  | `TimeoutStartSec` | `MemoryMax`, `OOMScoreAdjust` |
| ------------------- | -------------------------------- | ------------------------ | ----------------- | ----------------------------- |
| `swingset-acquire`  | `*:0/5`, `RandomizedDelaySec=20` | `acquireBudget` (`4m`)   | 10 min            | 1G, -200                      |
| `swingset-derive`   | `*:2/5`, `RandomizedDelaySec=20` | `deriveBudget` (`4m`)    | 30 min            | 5G                            |
| `swingset-plan`     | `*:10/30`                        | `planBudget` (`10m`)     | 20 min            | 2G                            |
| `swingset-release`  | `*:20` hourly                    | `releaseBudget` (`30m`)  | 60 min            | 7G, 500                       |
| `swingset-maintain` | daily 03:30                      | `maintainBudget` (`20m`) | 30 min            | 2G                            |

All are `Persistent=true`, skip while `operator-hold` exists and run the
watchdog when they fail. Derive gets `TimeoutStopSec=120`, the others 45 s.
Each memory cap bounds its own unit, not the sum: together they allow
17 GB on the 8 GB machine. On the Mac a build peaked at 6.3 GB and a
parse-only derive tick beside it at 2 to 3.9 GB, so derive starts no unit
while release builds (it holds `heavy.lock`). When memory runs out the
kernel kills `swingset-summary` first (`OOMScoreAdjust=800`), then release
(500). Switch 2b is gated on resident memory, the `peak_rss_bytes` of the
cycle's summary and of the loop records: release's 7G cap must be at least
1.15 times the cycle's peak over cycles that built, and release plus derive
during a build must stay under about 7 GB
([phase 2 deploy steps](../plans/architecture.md#9-migration-plan)).
systemd's `MemoryPeak` counts page cache charged to the unit, which reading
the database file fills, so it is information only. Acquire's 1G was sized
on a mock that returned no bodies; it is unverified until step 8 records
acquire ticks that fetched real pages.

Any switch while the loops are on, the rollback to the cycle included,
writes `operator-hold` first, waits until `systemctl list-units
'swingset-*'` shows no active unit, switches and removes the hold. systemd
stops a removed or changed unit with SIGTERM and kills derive after 120 s;
a staged history unit can take longer. The tick notes the stop request
(`runs/<run>.stop`), so that kill is not counted against the unit, but the
hold avoids cutting the unit off at all. With the loops on, the watchdog runs with
`--loops 1`: it checks all five timers and the loop records instead of the
cycle's ([alerts](operation.md#alerts)).

Every unit's `ExecStartPre` runs `uv sync` under `flock -w 600
<stateDir>/venv.lock`, loops on or off, so two units starting together after
a deploy do not install into the one venv at once. A sync that waits ten
minutes fails its unit.

Use `jj status` before evaluating newly created files with a Git-backed flake.
`path:.#orb` also works during initial bootstrapping. The host configuration
imports `/etc/nixos/configuration.nix`, which includes the generated container,
network, user, certificate, and OrbStack settings. Importing only
`orbstack.nix` omits the container and network setup. Do not make the generated
configuration import the flake host file back into itself.

The Mac and Linux share the checkout. Use a separate venv inside Linux:

```sh
export UV_PROJECT_ENVIRONMENT=/home/skeswa/swingset-venv
nix develop
uv sync --frozen
uv run python -c 'import pyarrow, duckdb, scipy, rapidfuzz, selectolax, httpx'
```

The service uses `/var/lib/swingset/venv` and its own uv cache. Both can be
rebuilt. Code and Python dependencies come from the flake and frozen lock;
overrides are captured from `services.swingset.overridesDir` every cycle.
The manifest records the clean repository revision or an explicit uncommitted
source-store identity. Local development falls back to a digest of the Python
sources. A code identity change invalidates the build; parser and projector
changes still require their respective version bumps. Each stage's code scope
and `recipe/env` also detect changed bytes when a manual version stays the same
([D-0207](../../journal/decisions/0207-scope-code-recipes-by-import-closure.md)).
`recipe/env` reads the interpreter, the installed distributions and
pyproject's `[project]` table. A deploy that changes any of them (a runtime
dependency, the interpreter) re-parses every snapshot, about 33,900, for 4
to 12 hours with fetching held by the parse backlog; batch such changes. A
`[tool.*]` edit or a dev-tool bump in `uv.lock` re-parses nothing.
