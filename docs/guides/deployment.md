# Installing the NixOS worker

This guide describes the existing OrbStack setup. Read [current status](../status.md)
and preserve any operating hold. Local development steps are in the
[development guide](development.md). The PostgreSQL/Dokploy target has a
[separate plan](../plans/postgres-migration/README.md).

## Development and VM setup

Build production only from the reviewed commit, never from the shared
checkout's working copy: it holds unreviewed and undeployed phases (today
phases 3, 4 and 5 with no cycle, while production runs switch 2a at schema
34), and a switch from it would migrate and change orchestration at once.
Switch from a workspace at that commit, for example
`jj workspace add --revision <commit> /Users/skeswa/swingset-deploy` and
`sudo nixos-rebuild switch --flake /Users/skeswa/swingset-deploy#orb --impure`,
or name it in the flake reference:
`--flake 'git+file:///Users/skeswa/repos/skeswa/swingset?rev=<commit>#orb'`.
A hotfix to the deployed switch branches from the deployed commit, never
from `@`. The commands below show the layout; replace `.` with the reviewed
tree.

```sh
orb create --cpus 4 --memory 8G nixos:25.11 swingset
orb config set machine.swingset.disk_bytes 274877906944
orb -m swingset
cd /Users/skeswa/repos/skeswa/swingset
sudo nixos-rebuild switch --flake .#orb --impure
systemctl list-timers 'swingset-*'
journalctl -u 'swingset-*' -f
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
overrides are captured from `services.swingset.overridesDir` every tick.
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

## The loops

The module runs five oneshot services and timers and nothing else orchestrates
([D-0215](../../journal/decisions/0215-loops-share-the-state-under-a-reader-writer-lock.md),
[D-0239](../../journal/decisions/0239-the-loops-are-the-only-orchestration.md),
[scheduling: loops](../reference/scheduling.md#loops)). `dryRun` picks
`--dry-run` or `--publish` for release. A loop never migrates while others
run: a tick that finds an older schema tries one exclusive open, which
migrates, or skips.

History: switch 2b turned the loops on with `loops.enable`, after the same
package had migrated to schema 34 and drained under the cycle
([phase 2 deploy steps](../plans/architecture.md#9-migration-plan)). Phase 5b
removed `swingset-cycle`, `swingset cycle`, `loops.enable`, `cycleBudget` and
`watchdogQuietSeconds`; a host file that still sets one of the three options
fails the build with a message naming D-0239, so delete the line. 5b alone
changes no schema and no parse scope and rolls back to the previous system
generation, but the tree that holds it also holds 5a, so it deploys as
[one switch with 5a](#the-phase-5-switch-5a-and-5b-together).

| Unit                | Timer                            | Budget option (default)  | `TimeoutStartSec` | `MemoryMax`, `OOMScoreAdjust` |
| ------------------- | -------------------------------- | ------------------------ | ----------------- | ----------------------------- |
| `swingset-acquire`  | `*:0/5`, `RandomizedDelaySec=20` | `acquireBudget` (`4m`)   | 10 min            | 1G, -200                      |
| `swingset-derive`   | `*:2/5`, `RandomizedDelaySec=20` | `deriveBudget` (`4m`)    | 30 min            | 5G                            |
| `swingset-plan`     | `*:10/30`                        | `planBudget` (`10m`)     | 20 min            | 2G                            |
| `swingset-release`  | `*:20` hourly                    | `releaseBudget` (`30m`)  | 60 min            | 7G, 500                       |
| `swingset-maintain` | daily 05:30                      | `maintainBudget` (`60m`) | 90 min            | 2G                            |

All are `Persistent=true`, skip while `operator-hold` exists and run the
watchdog when they fail. Derive gets `TimeoutStopSec=120`, the others 45 s.
Each memory cap bounds its own unit, not the sum: together they allow
17 GB on the 8 GB machine. On the Mac a build peaked at 6.3 GB and a
parse-only derive tick beside it at 2 to 3.9 GB, so derive starts no unit
while release builds: every build holds `heavy.lock` while `[release]
streamed_lock` is true (the default). When memory runs out the
kernel kills `swingset-summary` first (`OOMScoreAdjust=800`), then release
(500). Switch 2b is gated on resident memory, the `peak_rss_bytes` of the
cycle's summary and of the loop records: release's 7G cap had to be at least
1.15 times the cycle's peak over cycles that built, and release plus derive
during a build must stay under about 7 GB
([phase 2 deploy steps](../plans/architecture.md#9-migration-plan)).
systemd's `MemoryPeak` counts page cache charged to the unit, which reading
the database file fills, so it is information only. Acquire's 1G was sized
on a mock that returned no bodies; it is unverified until step 8 records
acquire ticks that fetched real pages.

Any switch, a rollback to a generation with the cycle included, writes
`operator-hold` first, waits until `systemctl list-units
'swingset-*'` shows no active unit, switches and removes the hold. systemd
stops a removed or changed unit with SIGTERM and kills derive after 120 s;
a staged year unit can take longer. The tick notes the stop request
(`runs/<run>.stop`), so that kill is not counted against the unit, but the
hold avoids cutting the unit off at all. The watchdog's wrapper passes the
loop timers that are not active (`--timers-inactive`) and the checks read the
loop records ([alerts](operation.md#alerts)).

Every unit's `ExecStartPre` runs `uv sync` under `flock -w 600
<stateDir>/venv.lock`, so two units starting together after
a deploy do not install into the one venv at once. A sync that waits ten
minutes fails its unit.

## The phase 3 switch

Phase 3 splits history and the map into year, mapping and resolution units,
gives links read sets and builds releases from partition artifacts
([phase 3 deploy steps](../plans/architecture.md#9-migration-plan)). It
changes the project, link and build code scopes and no parse or extract
scope. Migrating to schema 35 takes about 2.5 to 3 minutes on a cold copy
(R0: 144 to 171 s, most of it the `PRAGMA foreign_key_check` every migration
runs) under the writer lock. Do it during the hold, after `nixos-rebuild
switch` (the new package is on the worker only then, and timers skip while
the hold exists): `sudo -u swingset swingset migrate --lock-timeout 3600`
prints the schema before and after and the seconds, for the preflight
receipt. Run it as `swingset`, never root: root-owned `state.sqlite-wal` and
`-shm` files stop every loop from opening the database; check the owner with
`ls -l /var/lib/swingset/state.sqlite*` before ending the hold. Phase 3 is
the first production code that imports DuckDB, so also check, still under
the hold and after `migrate` (whose `uv run` syncs the venv), that it loads
in the units' environment: `sudo -u swingset env $(systemctl show -p
Environment --value swingset-release.service)
/var/lib/swingset/venv/bin/python -c 'import duckdb;
print(duckdb.connect().execute("select 42").fetchone())'` prints `(42,)`. If
it fails, every partitioned build fails: stay on the hold and roll back by
the byte copy. Check that `migrate` printed `schema_before` 34: on a wrong
`--state` path it creates an empty database and prints `null`. Left to the
loops, the first tick of any loop migrates and the others skip that tick.
The tree ships `inventory_interval_seconds = 43200` (D-0227's 48 hours and
D-0228's 7 days end with this switch); a phase 2 tree or hotfix built before
it keeps 172,800 for history and 604,800 for the inventory in its own tree. Derive then re-runs every project and link unit
once; output cutoff keeps their ids, and the new units mint once. Every build
holds the heavy lock while `[release] streamed_lock` is true (the default).
Once the post-check shows the release record's `peak_rss_bytes` under 2 GB
with derive active (each tick is its own process, so that is the build's
resident peak; `MemoryPeak` counts page cache and stays
information only, as in phase 2), a `nix/`-only switch can drop release's
`MemoryMax` from 7G to 3G, and a config switch can set `streamed_lock =
false`. For an assembly bug,
[`[release] path = "whole"`](operation.md#release-path) is a fallback only if
its measured peak fits release's cap; `closure` is none after the switch
(F30). Anything else, and returning to phase 2, takes the preflight's byte
copy ([rolling back a migration](operation.md#rolling-back-a-migration)).

## The phase 5 switch (5a and 5b together)

Phase 5a removes unit admissions, requirement history, the event-site review
stage and the origin fallback's online side; 5b removes the cycle
([phase 5 deploy steps](../plans/architecture.md#9-migration-plan)). Both
sit in one change, so they deploy as one switch: the module runs the five
loop units unconditionally and no cycle unit. Preconditions: phases 3 and 4
deployed and post-checked; switch 2b has run the loops 7 days with phase 2's
acceptance met (no `bounded_write` alarm in a week, the stop-marker item
settled); `nix/hosts/orb.nix` diffed against the deployed generation's
file, with every setting 2b's week added carried over except
`loops.enable`, `cycleBudget` and `watchdogQuietSeconds`, which fail the
build with D-0239's message; the phase 5 rehearsal receipt and the
independent check; and, before writing `operator-hold`, `df` on the Mac
showing at least production's `state.sqlite` size plus 20 GB free (free an
older copy first if short). Never switch to this
tree from 2a or before 2b's week: it would turn on all five loops at once,
skipping 2b's memory gate. It changes the project, link and build code
scopes and `config/sources.toml`, no parse or extract scope. Migration 37
deletes rows: every `work_` admission and its dependencies, the four
`requirement_*` history tables, and it closes the open `work_attempt` and
`history_event_site_review` findings. Before the script,
`migrate` settles any active admission as `process_interrupted`, or a
publication as `uncertain`; reconcile an `uncertain` publication before the
switch, so the preflight stops on one.

Migrate during the hold, after `nixos-rebuild switch`, as `swingset`:
`sudo -u swingset swingset migrate --lock-timeout 3600`. Check that it
printed `schema_before` 36 and `schema_after` 37. On a Mac clone of the
2026-09-29 copy 36 to 37 took 184 s with a 353 MB WAL peak
([phase 5 rehearsal](../../journal/evidence/runtime/architecture-phase5-rehearsal-2026-10/receipt.json)). Then,
still under the hold, read the result:

```sh
sudo -u swingset /var/lib/swingset/venv/bin/python - <<'PY'
import sqlite3
db = sqlite3.connect("file:/var/lib/swingset/state.sqlite?mode=ro", uri=True)
for sql in (
    "PRAGMA user_version",
    "SELECT count(*) FROM execution_admissions WHERE substr(action_id,1,5)='work_'",
    "SELECT count(*) FROM execution_dependencies WHERE substr(action_id,1,5)='work_'",
    "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'requirement%'",
    "SELECT count(*) FROM findings WHERE owner_kind='requirement' AND kind='work_attempt' AND closed_at IS NULL",
    "SELECT count(*) FROM findings WHERE kind='history_event_site_review' AND closed_at IS NULL",
    "SELECT action_kind, state, count(*) FROM execution_admissions GROUP BY 1, 2",
    "PRAGMA freelist_count",
    "PRAGMA page_size",
):
    print(sql, db.execute(sql).fetchall())
PY
```

Expect `37`, `0`, `0`, only `requirement_scan`, `0`, `0`, the preflight's
non-unit admission counts by kind unchanged (active ones now settled), and free pages times the page size
of about the deleted rows' size (1.95 GB on the clone). Check the owner with `ls -l
/var/lib/swingset/state.sqlite*`. `gc --reclaim` frees the pages later
under its own hold. Remove the summary's old cursors, which nothing reads or
deletes since migration 37: `sudo rm -f /var/lib/swingset/reports/requirements-*.json`.
Check that no `swingset-cycle` unit is loaded and that the five loop timers
and the watchdog timer are active. If any check fails, stay on the hold and
roll back by the byte copy ([rolling back a migration](operation.md#rolling-back-a-migration));
phase 4 code refuses 37, and the previous system generation alone is no
rollback once migrate ran.

After the hold ends, confirm within the first hour that acquire writes new
`scheduler_requests` rows and stores pages, with a 60-minute background
wait per `AGENTS.md`, before the hold owner signs off: the rehearsal's
acquire issued no request (D-0190). The publication path ran on a
schema-37 clone against a fake hub (rehearsal R5, 2026-10-04): the
`publication_narrow` admission active then settled, the narrow fence held,
and a SIGKILL during the upload left it for the next release tick, which
settled it `uncertain`, reconciled and published. `dryRun` stays under
[D-0189](../../journal/decisions/0189-stop-publishing-until-identity-output-is-recovered.md).
