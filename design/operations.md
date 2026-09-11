# Operations

## Host

The pipeline runs on a Linux box as `nix/module.nix`. v1 uses an OrbStack
NixOS VM on the development Mac; [implementation plan](implementation-plan.md#2-environment)
owns that setup. The module owns the service user, state directory,
environment file, toolchain, units, and timers. The VM uses UTC. The
same service module installs on production later.

The service uses Python, uv, node, and native libraries from the flake.
`ExecStartPre` runs `uv sync --frozen --no-dev` into the state's disposable
venv. Upgrading code means advancing the checkout with jj and rebuilding
NixOS. `services.swingset.overridesDir` points to the checkout's overrides;
a correction needs only a checkout update. Cycles capture those files
as described in [local state](state.md#invalidation).

The unit starts dry (`dryRun = true`) until the owner provisions
`HF_TOKEN` and explicitly sets `dryRun = false`. The token's presence
alone never enables publication. State layout is defined in
[local state](state.md#state-directory).

## Timers

| Unit                     | Schedule                                                    | What it runs                  |
| ------------------------ | ----------------------------------------------------------- | ----------------------------- |
| `swingset-cycle.timer`   | every 15 min, `RandomizedDelaySec=120`                      | `swingset cycle --budget 12m` |
| `swingset-backup.timer`  | Mon-Thu 04:00; Fri-Sun 04:00, 12:00, 20:00 (box local time) | `swingset backup`             |
| `swingset-summary.timer` | daily 08:00                                                 | `swingset summary`            |

Timers use `Persistent=true`. The 15-minute timer is the floor;
[scheduling](scheduling.md) decides what is due. Backup lock waits do
not count as completed backups. The backup service retries failures
with `Restart=on-failure` and `RestartSec=60`; doctor reports its last
successful checkpoint so retrying cannot conceal stale backups.

## Cycle

1. Acquire the state writer lock. Refuse normal work while
   `RESTORE_PENDING` exists. Capture and accept changed input files.
2. Reconcile any pending publication before creating another candidate.
   This runs even when no fetch or build is due. Respect dry-run mode.
3. If parse, project, or link work is already pending, skip adding a
   fetch batch this cycle. Otherwise discover seed watches and fetch
   due requests within the remaining wall-clock budget.
4. Drain parse, project (map first), and link work, in that order.
   Check the budget between units. A handled parser failure completes
   its unit with evidence; other failed units remain pending.
5. Only after those queues drain, build when the input/baseline pair
   needs work and publish when semantic content differs. Otherwise
   leave work for the next cycle. A dry run can build but never commits.
6. Write `runs/<run_id>.json` and release the lock.

There is no "nothing fetched, skip later stages" rule. File changes,
manual findings, and queued work are independent reasons to run. The
cycle runner consumes [durable work](state.md#invalidation); it does not
carry another stage dependency graph. Existing work takes precedence
over another fetch batch so a registry sweep cannot starve publication.

## Locks and operator commands

One advisory lock in the state directory excludes cycle, backup, restore,
and manual data mutations. The process holds it through each command;
the kernel releases it on death. Read-only doctor and summary use a
consistent database read and do not acquire the writer lock.

| Caller finding the lock held                                           | Required behavior                                                                                        |
| ---------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------- |
| Timer-triggered duplicate cycle                                        | Exit 0 with an explicit skipped-overlap log                                                              |
| Manual mutation, including pause, resume, sweep, reparse, or fetch-one | Wait up to `--lock-timeout` (default 60 s); timeout exits nonzero and says no change was applied         |
| Backup                                                                 | Wait for the lock; interruption or upload failure is nonzero and retried by the service                  |
| Restore                                                                | Wait up to the explicit timeout; require timers disabled and the former writer stopped before activation |

Every data-writing command except restore refuses a state directory
marked `RESTORE_PENDING`; doctor remains available for diagnosis.
Manual mutation success means its transaction committed. There is no
successful skipped pause or sweep. Tests hold the lock, start pause,
release it, and assert the pause is persisted before success. A separate
test exhausts the timeout and asserts nonzero exit and unchanged state.
Backup tests overlap a cycle and prove the checkpoint eventually runs.

`swingset pause --all | --host <h> | --source <s> [--until <time>]`
records an operator pause; `resume` removes only the selected operator
pause. Automatic throttle and block pauses remain in host state and
cannot be cleared accidentally by resume. Operator pauses are checked
before requests to the affected sources; all post-fetch stages still
run so an override can publish while fetching is paused. An indefinite
pause is a row without an expiry. Operator pauses are not run failures.

A pause command can wait behind an active cycle; it does not claim to
interrupt that cycle. To stop immediately, stop the active cycle service
and its timer, then record the pause. Stopping only the timer prevents
future starts and does not stop the active cycle. Resume has no catch-up:
each overdue watch is checked once, subject to normal budgets and
priority, then follows its ordinary schedule.

## Interruption and recovery

Each unit commits output, downstream work, revision changes, and its own
completion in one transaction. Artifacts are durable before database
references; publication intent is durable before the remote call. See
[local state](state.md) and [publishing](publishing.md#candidate-and-baseline).

On SIGTERM, stop issuing requests, finish or abandon the in-flight
request within its timeout, and stop at a unit boundary. Write a stopped
run summary when possible and exit 0 after a graceful stop. An unhandled
failure exits nonzero. `KillMode=mixed` and `TimeoutStopSec=45` bound the
service stop; forced termination rolls back any unfinished transaction.
A missing final run log after a hard kill is diagnostic loss, not proof
that a data transaction failed. The cycle budget applies to every stage.

A crash harness injects termination before and after each transaction,
artifact completion, and publication marker transition, then restarts.
Assert the same canonical belief, referential integrity, drained work,
and public changelog as an uninterrupted run with the same inputs.
Compare logical results; retry attempts, run ids, logs, timestamps, and
harmless orphan files can differ. Verify one remote commit per candidate,
including lost responses, rather than one HTTP attempt. The harness
also sends SIGTERM and checks graceful completion or rollback of the
current unit. It runs offline in WP3 and WP5.

## Backup and restore

A backup is a complete recoverable checkpoint, not a list of files that
happened to change. While holding the writer lock, make a SQLite backup
API copy and a checkpoint manifest listing each required relative path,
size, and SHA-256, plus the baseline target, pending candidate id if any,
schema and implementation versions, and selected input bundle hash.
Include the following closure of referenced artifacts:

- All raw and manual blobs referenced by snapshots or findings.
- All derived extracts referenced by snapshots.
- Captured input bundles referenced by current state or retained candidates.
- The complete baseline candidate and any pending candidate, including
  `BUILT`, `PUBLISHING`, and `PUBLISHED` when present. A receipt without
  promotion still belongs to a pending candidate.
- Any complete disposable candidate retained by the checkpoint, plus
  run summaries. Disposable candidates may instead be omitted; there
  is no database build-completion record to repair.

Validate every reference against the manifest. The Hub transport puts the
manifest outside a deterministic uncompressed tar containing the checkpoint
files, with a small transport manifest that records the tar's size and SHA-256.
This keeps large registry checkpoints to three upload operations and lets Xet
deduplicate unchanged tar blocks. Upload all three files in one archive commit;
restore also accepts older commits that stored every checkpoint file separately.
Reuse already uploaded content
only after confirming it exists at the archive's selected parent commit.
`backup_uploads` is an optimization, never evidence that a missing file
exists remotely. Record success only after acknowledgment. A failed
upload or lock interruption is retried; an unchanged checkpoint needs
no new commit. The venv, uv cache, lock file, incomplete candidates, and
unreferenced orphan blobs are excluded. Secrets are never captured.

Restore is an activation protocol:

1. Disable timers and stop the former writer. Select a specific archive
   commit, download into an empty staging state directory, and create
   `RESTORE_PENDING` before any service can use it. Resume verification
   after interruption while keeping that marker.
2. Verify the manifest's files, hashes, reference closure, SQLite
   integrity and foreign keys, schema compatibility, and saved input
   bundles. A missing extract or pending candidate is an error. Rebuild
   disposable runtime dependencies separately.
3. Recreate the baseline symlink and read the public repo head, even
   when a local publication receipt exists. A saved pending receipt must
   match that head; a mismatch is an error, never an unsent intent to
   retry. If the head equals the
   saved baseline, retain it. If a pending candidate exactly matches
   head by expected parent, candidate id, manifest, and file hashes,
   finish its acknowledgment and promotion locally. If its commit did
   not land and head still equals its expected parent, retain the intent
   for a later real cycle; restore itself creates no public commit.
4. If head is ahead of those recoverable states, or differs in any other
   way, keep `RESTORE_PENDING`, report expected and actual SHAs, and
   fail without publishing. A matching newer checkpoint can complete
   recovery. If none exists, the operator must recover the missing
   private state or explicitly decide how to handle data loss; normal
   restore never invents observations from public Parquet or resets
   history. Unknown third-party commits are also held for review.
5. Recheck the head before activation, rebuild upload receipts against
   the selected archive commit, and remove `RESTORE_PENDING` durably.
   The next cycle accepts the installed checkout's inputs against the
   saved bundle and enqueues any differences before proceeding. Enable
   timers only after verification succeeds.

Backup and public publication are separate commits. Therefore a machine
loss can lose private observations collected after its last checkpoint,
even when those observations were already published. v1's automatic
restore guarantee covers checkpoint state and its saved pending intent;
it cannot promise unattended recovery of unbacked private evidence.
The runbook must show the remote-ahead report and the recovery choices,
with publishing kept disabled until a choice is made and reconciled.

Acceptance tests restore after intent but before submission, after the
remote commit but before its receipt, and after receipt but before
promotion. Each uses the archived checkpoint alone and must preserve the
candidate and avoid duplicate commits. Also test a missing extract,
missing pending directory, stale upload receipt, interrupted restore,
a public commit made after backup, and an unrelated remote head. The
last two must leave publishing disabled without changing public history.

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
- A handled parse failure does not by itself fail the run. It marks the snapshot
  `parse_status = failed`, keeps the body and last-good observations,
  records the attempted versions, and completes that work item. A
  version change or explicit reparse can retry it; it does not spin
  forever on the same deterministic failure. The run summary
  lists failures. A parse failure rate above 10% for a source fails the
  run so it is noticed.
- A host paused for 403 or challenge fails the run with a clear message
  and is the first line of the next daily summary. This is the one
  condition that should interrupt a human.
- Review workflow: the owner reads `review_queue` (published in the
  dataset and printed by `swingset doctor`), adds rows to
  `overrides/*.csv`, and commits. The next cycle picks up the new
  overrides. Contributors can send pull requests against `overrides/`.
- `swingset doctor` prints last success per source, operator pauses and automatic host
  pauses separately, budget use, watches by state, pending work by
  stage, pending candidate, restore status, last backup commit and
  time, review size, and last acknowledged publish SHA.
