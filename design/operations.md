# Operations

## Host

The pipeline runs on a Linux box the owner already operates, as a NixOS
module in this repo (`nix/module.nix`). The module owns:

- a `swingset` system user and group;
- the state directory `/var/lib/swingset/` holding `state.sqlite`,
  `blobs/`, `last_published/`, `runs/`, and a checkout of `overrides/`;
- the environment file with `HF_TOKEN`;
- the systemd units below;
- the toolchain: Python 3.12, `uv`, `node`, and system libraries, from
  the flake.

The service runs `uv run swingset cycle` from a pinned checkout of this
repo with a committed `uv.lock`. Upgrading the pipeline is a `git pull`
plus a NixOS rebuild. The only impure step is uv resolving from the lock.

## Timers

| Unit | Schedule | What it runs |
|---|---|---|
| `swingset-cycle.timer` | every 15 min, `RandomizedDelaySec=120` | `swingset cycle --budget 12m` |
| `swingset-backup.timer` | Mon-Thu 04:00; Fri-Sun 04:00, 12:00, 20:00 (box local time) | `swingset backup` |
| `swingset-summary.timer` | daily 08:00 | `swingset summary` |

Timers use `Persistent=true` so a missed run fires after a reboot. Units
use a lock file under the state directory so a cycle never overlaps
another cycle or a backup. A cycle that finds the lock held exits 0 and
logs one line.

The 15-minute timer is the floor. The scheduler ([scheduling](scheduling.md)) decides what
is actually due, so weekday cycles usually check a handful of index pages
and exit in seconds.

Each cycle:

1. `swingset discover` if due.
2. `swingset poll --budget <n>`: fetch due watches in priority order
   (live, cooling, index, upcoming, archived, backfill), archive bodies.
3. `swingset parse`: parse new or re-flagged snapshots.
4. `swingset link`.
5. `swingset build`.
6. `swingset publish` if the diff is non-empty.
7. Write `runs/<run_id>.json`.

Steps 3 to 6 are skipped when step 2 fetched nothing new and no re-parse
is pending.

## Backup and restore

`swingset backup` pushes one commit to `skeswa/swingset-archive` with
`state.sqlite` (a consistent copy made via the SQLite backup API), every
blob not yet uploaded, `last_published/`, and `runs/`. Nothing is pushed
if nothing changed. Xet chunk-level dedup keeps the SQLite upload small.

Restore on a fresh box: install the module, run `swingset restore`,
which downloads the latest archive commit into the state directory, then
enable the timers. The runbook covers this.

## Secrets

`HF_TOKEN` with write access to both repos, in a root-only environment
file referenced by the units. Nothing else. No source needs credentials,
by design.

## Bootstrap and backfill

The registry sweep (16 hours) and the historical backfill (weeks) both
run inside normal cycles, driven by cursors in state, at the lowest
priority. `swingset sweep --start 1` seeds the registry cursor. No
special long-running job exists; the box is always on, so the cycle loop
is the long-running job.

## Observability

- Every run writes `runs/<run_id>.json` to the state directory: counts of
  watches checked, 304s, changed bodies, bytes fetched per host, parse
  errors, link status histogram, publish commit SHA, duration. These are
  included in the backup.
- Structured logs go to the systemd journal. `journalctl -u swingset-cycle`
  is the primary debugging tool.
- `swingset summary` writes a short daily digest: events in each watch
  state, new `review_queue` items, links upgraded and downgraded, sources
  paused, budget use per host, and the last publish. It goes to the
  journal and, if `summary_webhook_url` is set in config, to that URL as
  a JSON POST. The destination is the owner's choice and is not decided
  here.
- A failed parse does not fail the run. It marks the snapshot
  `parse_status = failed`, keeps the body, and continues. The run summary
  lists failures. A parse failure rate above 10% for a source fails the
  run so it is noticed.
- A host paused for 403 or challenge fails the run with a clear message
  and is the first line of the next daily summary. This is the one
  condition that should interrupt a human.
- Review workflow: the owner reads `review_queue` (published in the
  dataset and printed by `swingset doctor`), adds rows to
  `overrides/*.csv`, and commits. The next cycle picks up the new
  overrides. Contributors can send pull requests against `overrides/`.
- `swingset doctor` prints the state of every source: last success, pause
  status, budget used, watches by state.
