# Operating Swingset

Use this guide to inspect the worker, manage pauses, and request local work.
Read [current status](../status.md) before changing production. A token alone
does not enable publication; begin a new installation with `dryRun = true`.
The [current extension handoff](../../journal/investigations/2026/event-extension-operating-handoff-2026-09-17.md)
pins the held runtime, checkpoint and unfinished activation checks.

This guide describes the tree it sits in, which has no cycle (phase 5b).
Until the phase 5 switch, production runs an older tree, with the cycle under
switch 2a: for a production incident read the guide at the deployed commit,
`jj file show -r <deployed commit> docs/guides/operation.md`.

For other tasks, see [installation](deployment.md),
[backup and restore](backup-and-restore.md), or [data corrections](data-corrections.md).
The [operations reference](../reference/operations.md) owns worker behavior.

## Commands and logs

Data-writing commands use the same state lock. H13 pause and resume commands,
and the `hold` commands in [disk space](#disk-space), use the separate bounded
control path described below. A timer overlap exits 0 with
`skipped-overlap`. Manual mutations wait up to `--lock-timeout` (60 seconds by
default); a timeout exits nonzero and applies no change. Backup waits for the
writer and the service retries failures after 60 seconds. Each loop tick holds the state shared: such a command waits for running ticks,
and new ticks skip while it waits
([state: locks](../reference/state.md#locks)). Derive and acquire ticks run
four minutes or more and release up to an hour, so 60 seconds usually times
out. Run an exclusive command (`accept-year`, `gc --apply`, `sweep`,
`publish`) with `--lock-timeout 3600`, or write `operator-hold`,
wait until `systemctl list-units 'swingset-*'` shows no active tick, and
remove the hold after.

```sh
sudo -u swingset swingset doctor
sudo -u swingset swingset summary
journalctl -u 'swingset-*' --since today
systemctl list-timers 'swingset-*'
```

When running the CLI outside the installed service, pass absolute `--config`
and `--overrides` paths if the current directory is not the checkout.
Run state and control commands as the service user. Both `state.lock` and
`control.lock` need that user's read/write access. If a retained lock has the
wrong owner, stop ordinary jobs and inspect it before repair; never delete or
replace a lock file to fix permissions. See the
[H16 ownership repair](../../journal/decisions/0033-repair-production-control-lock-ownership.md).
`doctor` reads without taking the writer lock. It and `summary` report the
database's bytes (`retention.usage`) but walk the retention plan only with
`--retention`: the walk took 13 GB on the 35 GB copy, past the 8 GB worker, so
run that form on a Mac clone. Without it `summary` peaked at 4.5 GB there
(4.4 GB of it the publication report's closure check); its unit is capped at
5 GB (`MemoryMax`) and is the kernel's first choice when memory runs out;
a failed run raises `[summary-failed]`.
`runs/*.json` holds each tick's
counts, stages, host pauses, errors, and the graceful-stop flag. A hard kill can
lose the final run log; SQLite transactions and already durable artifacts remain
the recovery source.

H14 adds a `scheduler` section to doctor and summary. `initial_objectives`
contains policy targets; `observed_host_usage` retains all actual daily debits,
including requests before H14. `observed_attributed_service` counts requests
issued under the new policy. Offline attempt counts do not establish completed
requirements. Request wall ages include operator pauses.

The event-completion increment adds an `event_inventory` catalog. Its listed
page counts describe retained enumeration membership; they do not establish
that the files remain available or that results were published. For a fresh
local check of one event, use its source and source reference from the catalog:

```sh
swingset doctor --source SOURCE --source-event SOURCE_REF --json
```

The drill-down verifies the event's retained artifacts without downloading or
restoring files. Missing or corrupt files reopen their local stage. It shows
unknown pagination, publication, and eligible-service history explicitly.
It also shows recorded turns and request charges, plus current pause, retry,
host-budget and backlog facts. These facts can explain waiting; they do not
grant permission to fetch or reconstruct past eligibility.
`summary` accepts the same filter. The report also shows the operator-hold
marker. See [current status](../status.md) before expecting these fields on the
deployed worker.

The local extension adds `acquisition_timing` to this drill-down. It reports
closed observed waiting intervals and diagnostic alarms, with explicit gaps;
it does not report exact age since discovery. Historical dispatch and
interpretation timing remain unknown. See the
[timing contract](../reference/event-timing.md) before setting an objective.

Five loops run one tick each on their own timers, sharing the state with each
other ([scheduling: loops](../reference/scheduling.md#loops)); they are the
only orchestration, and no `swingset cycle` command or unit exists since phase 5
([D-0239](../../journal/decisions/0239-the-loops-are-the-only-orchestration.md)).
A tick also runs by hand:

```sh
sudo -u swingset swingset plan --timer                  # bookkeeping, acquisition_plan
sudo -u swingset swingset acquire --budget 4m --timer   # discovery and fetching
sudo -u swingset swingset derive --budget 4m --timer    # parse, project, link
sudo -u swingset swingset maintain --timer              # WAL checkpoint, pruning
sudo -u swingset swingset release --dry-run --timer     # reconcile, build (--publish to commit)
```

A tick of a loop already running, or while a writer holds or waits for the
state, skips (`skipped-overlap`, exit 0 with `--timer`). A tick that finds a
closure-mode publication in flight writes only its record, `skipped:
publication_in_flight`; a partitioned publication pauses only maintain and
release. While a build is due, release has made no progress for two build
intervals and its recent ticks were skipped because derive held the heavy
lock (`heavy_busy`), derive runs only parse and link units and keeps the
guard `loop:derive release_lag` (`release_lag_intervals`, 0 turns it off).
A release behind for another cause (stopped, paused, `disk`, not building)
is only reported in the derive record's `release_lag` with its `cause`:
holding project units would not help it, so fix the cause.
The record is `runs/<run_id>.json` with `loop` set; `loop_state` holds each
loop's latest tick. Exit 1 means the tick failed. Plan skips until derive has accepted the
current inputs; acquire fetches history pages only from plan rows marked
`eligible`, so it offers none until plan has run. Release skips the same way,
and also skips (`disk`, with a `loop:release release_disk_low` guard) while
`/run/swingset-disk` shows under `release_min_free_bytes` free (30 GB), or
(`heavy_busy`) when a build or a correction cannot take the heavy lock
within 15 minutes. A partitioned build takes it too while `[release]
streamed_lock` is true (the default); turn it off only after the worker's
post-check shows the release record's `peak_rss_bytes` under 2 GB with
derive active (each tick is its own process, so that is the build's; systemd's `MemoryPeak` counts page cache and is information
only). A reading
older than an hour is unknown (`disk.stale`) and refuses nothing. Waiting for
inputs and `heavy_busy` leave `loop:<name>` guards with 1 h and 3 h expiries,
cleared by the next tick past them.

Optional `[scheduling]` settings in `config/sources.toml` tune pressure
thresholds, high-water marks and intervals under the
[scheduling contract](../reference/scheduling.md); the removed cycle share
keys stop config loading. Host budgets and request floors remain independently
enforced.

## Tokens and enabling publication

For a reviewed correction while unrelated parsing is blocked, use
`swingset build --correction-only` with the usual state, config, and override
paths. It reads the acknowledged public baseline, accepts current correction
inputs, and builds a local candidate without source requests. Inspect its
manifest, card, and changed Parquet rows, then use
`swingset publish --correction-only` under the existing publication authorization.
The latter repeats the build and rechecks current inputs immediately before
the Hub commit. Pending parse/project/link work remains queued.

`doctor --json` reports admission states, the accepted identity journal, pending
reference migrations, and the latest publication receipt with correction latency.
Never resubmit a `REJECTED` candidate; accept corrected inputs and rebuild. A
pending intent with an uncertain network outcome is reconciled before any new
submission. An unlanded intent remains held while `RESTORE_PENDING` exists.

H10 adds public identity acceptance metadata before schema 1.0. Defaults keep
supported IDs from the published baseline and add new entry IDs that pass the
H17 rules ([D-0248](../../journal/decisions/0248-publish-new-identity-links-that-pass-the-h17-review-rules.md)). Judges without WSDC numbers remain named records with null IDs.

On schema 14, doctor derives unfinished projection and linking from captured
inputs and materialized generations. A missing queue row does not clear that
work. Migration leaves existing output unmaterialized until its actual work
completes; this is expected replay work, not evidence that the public dataset
lost rows. Runtime code and schema bytes are captured with the input bundle,
so a deployment may require replay without a manual version change. Host
allocations and request budgets remain separate from interpretation recipes.

Doctor's build materialization records describe durable local candidates.
Publication still requires its confirmed receipt. A candidate with missing or
corrupt files is rebuilt, including when its `BUILT` marker survives. The
H16 normal build selects compatible retained generations even when other
derivations remain unfinished. Current corrections, revocations, and the
acknowledged baseline still fence publication.

The owner creates `skeswa/swingset` (public dataset) and
`skeswa/swingset-archive` (private dataset), and, before segmented backups
upload, `skeswa/swingset-backup-sets` (private dataset; its history is
squashed, so it holds nothing else). Provision a write token for all of them in
a root-owned mode-0600 file outside the checkout, for example
`/etc/swingset.env`, containing `HF_TOKEN=...`. Never commit that file or copy it
into a state checkpoint.

Set `services.swingset.environmentFile = "/etc/swingset.env";` and rebuild.
After reviewing the first dry build, set `services.swingset.dryRun = false;`
and rebuild again. First check that the latest dry release record does not
show `correction_needed: true`: the first published tick would build and
publish that correction. To undo a bad publication, follow
[rolling back](data-corrections.md#rolling-back-a-publication); never revert
the hub by hand. An existing public dataset requires a matching private
checkpoint; bootstrapping accepts only `.gitattributes` and the generated ODC-By
license-only README. It refuses arbitrary existing content.

For rotation, stop the loop and backup services, replace the environment file,
rebuild if its path changed, then start the timers and run `doctor`. Revoke the
old token after the new token has completed a publish and backup. Token values
must not appear in logs or shell history.

Until credentials are provisioned, stop `swingset-backup.timer`; a failed or
local-only backup is not a successful remote checkpoint. A local checkpoint for
testing is explicit:

```sh
uv run swingset backup --local --state ./tmp/state --destination ./tmp/checkpoint
```

Remote checkpoints keep `checkpoint.json` outside a deterministic uncompressed
tar. `_transport/archive.json` authenticates the tar by size and SHA-256. This
keeps a registry-scale checkpoint to three files in one atomic Hub commit;
restore also accepts older checkpoints whose files were uploaded separately.

The backup service sets `TMPDIR=/var/tmp` so the temporary tar uses disk. Preserve
that setting in manually invoked checkpoint or upload helpers; for a transient
unit, pass `--setenv=TMPDIR=/var/tmp` to `systemd-run`. This host's `/tmp` is
memory-backed and cannot safely hold the full archive. A locally verified
checkpoint is not remotely acknowledged until the upload and manifest check
finish. Retain failed-attempt receipts and use a new attempt path when retrying.

### Local-only backups

The transport refuses a file over 20 GiB and a production checkpoint is about
36 GB, so no upload can succeed until segmented backups (architecture phase 4).
Production therefore sets `services.swingset.backupUpload = false`
([D-0203](../../journal/decisions/0203-back-up-locally-and-keep-one-checkpoint-until-segmented-backups.md)):

- The timer runs `swingset backup --local`. It needs no token.
- The room check wants one copy of the state (without `checkpoints/`) plus
  20 GB free, not two copies.
- Production `[retention]` keeps one timer checkpoint:
  `checkpoint_keep_recent = 1` and `checkpoint_max_age = "0s"`. Each
  successful backup removes every older `run_*` checkpoint and keeps the one
  it wrote; named ones stay. Only the newest copy is then left for
  `LocalCheckpointRecovery` to restore a lost blob or extract from.
- Before it prunes, a local backup verifies the new copy as a restore reads
  it: closure, file hashes, schema, foreign keys and artifact closure, with
  `PRAGMA quick_check` in place of `integrity_check`. This runs after the
  backup releases `state.lock`, so loop ticks do not wait; the prune takes the
  lock again. It logs `checkpoint-verify` with its seconds. On a clone of
  the 35 GB state on the Mac (2026-09-25) the read-back took about 6 minutes
  (`quick_check` alone 199 s, result `ok`; `integrity_check` 9.8 minutes),
  the whole local backup about 10 minutes, at 320 MB peak memory. Expect the
  worker to be slower; the unit stops a local backup after 6 hours. A copy
  that fails is renamed `failed-run_*` first, no good checkpoint is removed,
  the run record is rewritten as failed, and the backup fails. In
  `checkpoints/` only the newest failed copy is kept: the next failure
  removes the older one, and a removal that fails is logged
  (`failed-checkpoint-removal-failed`). With `--destination` elsewhere, the
  copy is only renamed. Inspect it, then remove it with
  `swingset backup --remove-checkpoint failed-run_...`. The backup prints
  the checkpoint path only after the copy passes. `--no-prune` skips the
  read-back too. This is a package change deployed with phase 1's switch;
  until then a local backup prunes without it, so the Mac byte copy is the
  fallback.
- The watchdog logs `[backup-upload-paused]` once a day, `[backup-stale]`
  when the newest complete `run_*` checkpoint is older than
  `backupMaxAgeSeconds` (36 hours) or missing, and `[backup-failed]` while
  the backup unit is failed (room check refused, copy or verification error)
  or a `failed-run_*` copy exists.
- The Mac byte copy is the only copy off the machine. Refresh it weekly.

Phase 4's backup sets replace these checkpoints once
`services.swingset.backupSegmented` is set: the timer runs `swingset backup
--segmented --local`, the room check wants the size of `state.sqlite` plus
20 GB, and each backup keeps the newest set and the newest drilled one under
`checkpoints/sets/`. The `checkpoint_` knobs above no longer apply, and the
old `run_*` checkpoint stays until an operator removes it (`rm -rf` as root
under the hold, after an independent check; `--remove-checkpoint` refuses
`run_*` names). `[backup-stale]` reads
sets too, and the weekly drill restores one
([backup sets](backup-and-restore.md#backup-sets-and-the-restore-drill)).
Uploads stay off until the owner turns `backupUpload` on (D-0232).

Switching production to local-only backups deletes about 94 GB of old
checkpoints, so follow this order. The one-copy room check needs about
69.5 GB. The candidate 019 preflight receipt showed 46.5 GB free at 16:10 UTC
on 2026-09-25, but free space moves with scratch copies; whenever it is above
69.5 GB, the first timer run after the switch writes a copy and prunes every
older `run_*` at once, and until phase 1's package that prune has no
read-back.

1. Refresh the Mac byte copy and verify it. Preflight receipt: system
   generation, quota free, `doctor` checkpoint list, pending counts by stage.
2. Independent check by a second agent, with a written verdict, of the
   deletion list: every `run_*` but the newest. It also lists the `blobs/` and
   `extracts/` paths in the older checkpoints' manifests but not the newest
   one's, and confirms live state references none of them (or that the
   artifact verification summary on live state shows zero missing).
3. The operator removes the approved directories as root (`rm -rf`; timer
   names cannot be removed with `--remove-checkpoint`), with a receipt of
   names, bytes and quota before and after. Then
   `systemctl reset-failed swingset-backup.service`: the refused backups left
   it failed, and `[backup-failed]` would fire until a backup succeeds.
4. `nixos-rebuild switch`. If the switch must come first, stop
   `swingset-backup.timer` before it and start it after step 3.
5. Post-check receipt: the next backup writes one checkpoint and keeps free
   space above `diskMinFreeBytes` after its prune; pending counts by stage are
   unchanged after the first derive tick.

A local backup briefly holds two checkpoints, so free space dips by about
36 GB during each one; while the backup unit runs, `[disk-low]` fires only
below the 20 GB headroom its room check kept. As the state grows,
`[disk-low]` stays on past a state size of about 59 GB and the room check
refuses past about 66 GB (rough estimates). Nothing alarms before then, so
each post-check receipt records the `[state-size]` measure (the state
without `checkpoints/`, `venv/` and `uv-cache/`) against the 55 GB phase 4
trigger in the
[architecture plan](../plans/architecture.md).

## Alerts

`swingset-watchdog` runs every 30 minutes and after any loop tick or backup
failure. It appends to `/var/lib/swingset/ALERTS.log`, logs at error level,
and pipes to `services.swingset.alertCommand` when set, for example
`curl -s -d @- https://ntfy.sh/<topic>`; the alert text arrives on standard
input. Read the log with `journalctl -t swingset-watchdog`
([D-0188](../../journal/decisions/0188-alert-on-failing-or-stalled-cycles-and-stop-re-queuing-on-config-switches.md),
[D-0202](../../journal/decisions/0202-the-watchdog-alarms-on-progress-within-two-hours.md)).

Each line starts with a key. A key already logged within
`watchdogRepeatSeconds` (two hours) is not sent again. For the gate keys,
`[host-failing]`, `[backup-failed]`, `[watchdog-blind]`, `[unit-quarantined]`, `[write-deadline]`,
`[guard-expired]`, `[loop-quiet]`, `[loop-failing]`, `[loop-idle]`, `[release-refused]`, `[read-set-miss]`, `[restore-drill-failed]`, `[collector-failed]`, `[collector-stale]`, `[storage-failed]`, `[storage-stalled]` and `[backup-incomplete]` only the same text is held back, so a newly blocked host repeats at once. With `alertCommand`
set, the unit also sends one `[watchdog-alive]` line a day to it, not to
`ALERTS.log`; a missing one means the watchdog timer or the notifier stopped.

| Key                          | Fires when                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  | Held |
| ---------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---- |
| `[watchdog-blind]`           | a check could not run twice in a row, a second apart (a missing table, a bad stamp); names each one. The other checks still run                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             | yes  |
| `[watchdog-error]`           | `watchdog.py` crashed or exited nonzero; the wrapper writes it, at most once per `watchdogRepeatSeconds`, and the script's stderr goes to the journal                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       | yes  |
| `[loop-failing]`             | a loop's last three ticks, skips and release ticks that only waited for the build interval or backlog left out, all failed; one line per loop with the latest error                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         | yes  |
| `[loop-idle]`                | a loop's `loop_state.last_progress_at` is older than its window while unheld and its timer active; skipped and idle ticks do not reset it. Acquire 1 h unless a host or acquire guard was seen in that hour, derive 2 h while ready work waits, plan 2 h, release its build interval plus 2 h unless its newest tick waited for the interval                                                                                                                                                                                                                                                                                                                                                                                                                | no   |
| `[loop-quiet]`               | a loop with an active timer has no tick finished for about three intervals: acquire and derive 30 minutes, plan 2 h, release 4 h, maintain 26 h. A loop with no tick on record counts from the oldest loop tick read. A tick skipped by the lock writes no record, so a loop always skipped is quiet too. Held back while a backup runs (`--backup-active 1`), since every tick skips then                                                                                                                                                                                                                                                                                                                                                                  | no   |
| `[plan-stale]`               | the newest `acquisition_plan` row is over two hours old while the plan timer is active; acquire offers only planned pages                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   | no   |
| `[unit-quarantined]`         | a unit failed three times in a row with one class on one input (twenty for `unit_io_error`) and is quarantined for seven days; one line per unit, `<stage>/<kind>/<id> after N x <class>: <error>`, or one line by class when over five units quarantined in the window. Each quarantine logs once: the window is two hours. See [the work queue](#work-queue-quarantine-and-requeue) ([D-0216](../../journal/decisions/0216-the-work-queue-leases-backs-off-and-quarantines.md))                                                                                                                                                                                                                                                                           | yes  |
| `[admission-stuck]`          | an execution admission (a request, fetch or operation) has been active over two hours; names up to five, with kind, run and admission time. A matching pause drains until a loop tick settles it: at once when its run has finished, after six hours when it has no loop run                                                                                                                                                                                                                                                                                                                                                                                                                                                                                | yes  |
| `[lease-stuck]`              | a work-queue lease ended over ten minutes ago and nothing took it over; names up to five units, owners and lease ends                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       | yes  |
| `[write-deadline]`           | a run record finished in the last two hours counts `write_deadline_fired` above 0: a write ran into the 60-second alarm and rolled back, which should never happen ([D-0217](../../journal/decisions/0217-large-generations-store-in-chunks-and-the-write-deadline-is-an-alarm.md)). One line per loop, or per command for a command's record, with the count, the latest run and the longest write. Each text logs once in the window                                                                                                                                                                                                                                                                                                                      | yes  |
| `[size-cap-pause]`           | the size-cap pause is active ([retention](#retention-plans-and-holds))                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      | yes  |
| `[hold-overdue]`             | the `operator-hold` file or an all-scope pause is older than 48 hours, or an all-scope pause has no `created_at` (age unknown); repeats daily                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               | yes  |
| `[timer-inactive]`           | nothing holds the worker and a loop timer is not active; names each. A stopped acquire timer silences only `[stale-fetch]`, a stopped derive timer only `[stale-progress]`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  | —    |
| `[stale-fetch]`              | no request for `staleFetchSeconds` (two hours); names the stored-page age, hosts that spent today's budget and the ready parse backlog, since backpressure holds requests                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   | no   |
| `[budgets-spent]`            | in place of `[stale-fetch]` when every host that fetched today has spent its daily request or byte budget; repeats daily                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    | no   |
| `[stale-progress]`           | a stage with ready units made no progress for `staleProgressSeconds` (two hours) since its work became runnable; one clock per stage, and one for `project/year`. Years are not paced. A queued inventory held by its interval, with the mapping, resolution, event and source-event units behind it, is not a stall ([D-0210](../../journal/decisions/0210-pace-the-inventory-like-history.md)); a year that needs it is clocked from no earlier than when the gate opens. A link whose event, resolution or year is queued waits and is not a stall; the line counts it. Other links are clocked from their queue time or when their event, resolution and years were last made, whichever is later. A current inventory that is not queued holds nothing | no   |
| `[gate-legacy-spacing]`      | a host's request spacing is unknown, so the gate skips it with no expiry ([D-0190](../../journal/decisions/0190-record-spacing-baselines-for-the-origin-hosts-from-the-eight-day-gap.md))                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   | yes  |
| `[gate-host-blocked]`        | a host is paused as `Blocked`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               | yes  |
| `[guard-expired]`            | a host or loop guard was still met after its expiry, and within the last two hours: a gate holds fetches longer than its reason should ([fetch guards](../reference/fetching.md#guards), [D-0219](../../journal/decisions/0219-every-guard-that-holds-a-fetch-has-a-reason-with-an-expiry.md)). One line per guard, `<subject> held by <reason> since ..., past its expiry at ...`, or one line by reason for over five                                                                                                                                                                                                                                                                                                                                     | yes  |
| `[guards]`                   | guard rows exist; one count by reason, at most once a day. Informational: watch guards (history gates, robots, a disabled source) show only here                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            | yes  |
| `[host-failing]`             | a host's last four or more responses were all 429 or 5xx; clears on its next success                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        | yes  |
| `[disk-low]`                 | the disk quota runs low ([disk space](#disk-space)); while a backup runs, only below its 20 GB headroom                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     | yes  |
| `[backup-failed]`            | `swingset-backup.service` is failed, or a `failed-run_*` checkpoint exists ([local-only backups](#local-only-backups))                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      | yes  |
| `[summary-failed]`           | `swingset-summary.service` is failed, possibly killed at its 5 GB memory cap (`MemoryMax`); the daily digest stops until a run succeeds                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     | yes  |
| `[backup-stale]`             | upload is off and the newest complete `run_*` checkpoint or backup set (`checkpoints/sets/*/backup.json`) is older than `backupMaxAgeSeconds` (36 hours), or there is none; with upload and segmented sets on, the newest set in the archive (`checkpoints/remote.json`) is older, or none has been uploaded                                                                                                                                                                                                                                                                                                                                                                                                                                                | yes  |
| `[backup-upload-paused]`     | upload is off; names the newest local checkpoint and its age; repeats at most once a day                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    | yes  |
| `[state-size]`               | the state directory without `checkpoints/`, `venv/` and `uv-cache/` holds more than 50 GB (`--state-size-alert`), 5 GB before architecture phase 4's trigger, which uses the same measure; repeats at most once a day                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       | yes  |
| `[size-cap-near]`            | `state.sqlite` is over 90% of `[retention] max_database_bytes` (68.4 GB of 76 GB since D-0233), where the size-cap pause stops the pipeline; repeats at most once a day                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     | yes  |
| `[replay-loop]`              | one kind's replay epoch moved in each of the last three derive ticks that were not skipped, and none wrote a generation of that kind or of a kind it reads: two kinds replaying each other. Unrelated generations, such as dancer changes, do not hide it ([D-0205](../../journal/decisions/0205-cut-off-identical-derivation-output.md))                                                                                                                                                                                                                                                                                                                                                                                                                   | yes  |
| `[report-failing]`           | a derive tick's report failed in one of the last three (`derivation_error`, `controls_error`) or a record lacks `derivation`, so `[replay-loop]` cannot see it; names the latest error                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      | yes  |
| `[equivalences]`             | `derivation_equivalences` or `source_equivalences` holds more than 500,000 rows (counted, so the line clears once collection deletes them); nothing collects them before invalidation step 3, which then starts ahead of phase 2 ([D-0208](../../journal/decisions/0208-keep-the-64-gb-cap-until-closure-collection.md)); repeats at most once a day                                                                                                                                                                                                                                                                                                                                                                                                        | yes  |
| `[wal-large]`                | `state.sqlite-wal` holds over 4 GB of frames not yet reset: checkpoints are not keeping up, usually behind a long read. The file never shrinks, so frames from before the last restart do not count                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         | yes  |
| `[building-orphans]`         | a staged generation in `derivation_building` has not advanced for 24 hours; names up to five ([staged completion](../reference/state.md#derivation-generations-h15))                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        | no   |
| `[incomplete-generations]`   | more than three partial generations with no building row still hold references (maintain's daily `holding` count); evicted generations are whole and not counted, and the weekly collector strips the references of those over a day old ([maintain storage tasks](../reference/operations.md#maintain-storage-tasks))                                                                                                                                                                                                                                                                                                                                                                                                                                      | no   |
| `[admissions-growth]`        | maintain's `admissions_rows` is over 600,000 (migration 37 leaves about 265,000): request rows stay while `scheduler_requests` names them, and that table has no collector yet ([maintenance item 15](../plans/README.md#maintenance-queue), D-0238)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        | no   |
| `[release-refused]`          | the newest gate verdict of a release tick or build command in the last seven days refused its candidate: nothing is published until a build passes or `overrides/release_decreases.csv` approves the decrease ([approving a decrease](data-corrections.md#approving-a-decrease), [D-0225](../../journal/decisions/0225-publish-only-strictly-better-releases.md)). Names the candidate and reasons; repeats daily, and at once for another candidate or reason                                                                                                                                                                                                                                                                                              | yes  |
| `[partitions-withheld]`      | stage 1 withholds partitions; one count by reason, daily, and at once when the count rises by more than 10 over the last line's. `swingset partitions --withheld` lists them with their detail; the count adds the newest release record's `partitions.assembly_withheld` (`assembly_check:<check>`), whose rows still say built                                                                                                                                                                                                                                                                                                                                                                                                                            | yes  |
| `[partitions-stale]`         | partitions that have published a kept artifact for over 48 hours while their event, resolution or link is not current (`kept_not_current_since`): a quarantined or unqueued unit; names up to five, daily                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   | yes  |
| `[artifact-collector-blind]` | maintain's newest collector could not read a retained release manifest, so it removed nothing; daily until a tick reads them all                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            | yes  |
| `[read-set-miss]`            | the newest weekly verify sample (maintain, last nine days) found a current unit whose re-derived output differs from its generation, or whose re-run raised `NondeterministicOutputError`: a read set misses an input ([verify sample](../reference/operations.md#verify-sample)). Names up to five units; repeats daily until a clean sample. Maintain also appends its own `[read-set-miss] <unit>` lines                                                                                                                                                                                                                                                                                                                                                 | yes  |
| `[verify-failures]`          | units of the newest verify sample failed to re-derive for another reason, so they were not verified; names up to five; repeats daily                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        | yes  |
| `[verify-overdue]`           | the maintain loop has run for over nine days and no verify sample completed in them (each kind reaching its target); maintain samples again each tick until one does                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        | yes  |
| `[read-set-widened]`         | in the last day a derive tick counted `undeclared_reads`, `read_set_widened`, `pool_probe_widened` or `dancer_read_after_load`; each should be 0. A widened link reads a whole table and re-runs on any change of it ([read recorder](../reference/state.md#read-recorder)); repeats daily                                                                                                                                                                                                                                                                                                                                                                                                                                                                  | yes  |
| `[artifact-orphans]`         | partition artifacts no current partition names, and release manifests over a week old, hold over 1 GB: the daily collector is behind, or retained release manifests hold many; repeats daily                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                | yes  |
| `[collector-failed]`         | maintain's newest full collector sweep (last nine days) raised; names the error; daily until a sweep passes. Nothing past the error was removed ([maintain storage tasks](../reference/operations.md#maintain-storage-tasks))                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               | yes  |
| `[objects-size]`             | `objects/`, `blobs/`, `extracts/` and `partitions/` hold over 60 GB together; daily                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         | yes  |
| `[restore-drill-failed]`     | the newest restore drill of a backup set failed; names the set and the error; daily until a drill passes. The drill appends its own line with other text and keeps its scratch under `/var/tmp/swingset-drill-<set>` ([restore drill](../reference/operations.md#restore-drill))                                                                                                                                                                                                                                                                                                                                                                                                                                                                            | yes  |
| `[restore-drill-stale]`      | backup sets exist and none passed a restore drill in 8 days (counted from the first set when none ever passed); see the `loop:maintain drill_no_room` guard; daily                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          | yes  |
| `[collector-stale]`          | phase 4's storage tasks have run for over `collect_every_days` plus 2 days (9) and no collector sweep completed in them; names what stopped each class in the newest sweep (the budget, a publication, a refusal); daily                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    | yes  |
| `[storage-failed]`           | maintain's newest tick recorded a storage task (`artifacts`, `backup_scratch`, `evict`, `pack_bodies`, `collector`, `drill`) that raised; names it and the error. The other tasks ran ([maintain storage tasks](../reference/operations.md#maintain-storage-tasks))                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         | yes  |
| `[storage-stalled]`          | maintain's `evict` or `pack_bodies` left a backlog and made no progress in each of the last three ticks; names failing generations if any (their guards `loop:maintain evict_failed`, `pack_failed`)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        | yes  |
| `[backup-incomplete]`        | the newest backup set was written without files the state names (`missing` in its `backup.json`); restoring or drilling it refuses; what puts each class back is under [lost files](backup-and-restore.md#lost-files); daily                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                | yes  |
| `[archive-size]`             | upload mode with segmented sets: the archive holds over 40 GB, or its collector failed after the last upload (`checkpoints/remote.json`); daily                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             | yes  |
| `[release-lag]`              | derive's `loop:derive release_lag` guard was seen in the last two hours: release is behind because derive held the heavy lock, so derive runs only parse and link units until release catches up ([release path](#release-path))                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            | no   |

"Held" says whether the line still fires while held. Held means the
`operator-hold` file exists or an all-scope operator pause other than the size
cap is active. An inactive acquire timer silences `[stale-fetch]` and an
inactive derive timer `[stale-progress]`; each raises `[timer-inactive]` instead. A scoped pause (source, kind or host) is not a
hold: if it holds all ready work, `[stale-progress]` fires, so use an
all-scope pause or `operator-hold`. A gate that skips every request is not a
failure, so only the fetch and progress lines report it, and
`[guard-expired]` once its reason outlives its expiry. Requests count, not
stored pages: a 304 stores nothing. A ready unit is one whose latest attempt
is absent or succeeded, has a retry requested since, or is transient or
interrupted with its retry time past, and whose work-queue row, if any, is
leased or `ready` and due. A unit waiting for input, quarantined, or backing
off is not ready and raises nothing; `[unit-quarantined]` reports the
quarantines. A derivation that raised is transient with a retry clock of 15
minutes to six hours, so it is ready again when that passes. A stage's clock is the later of its last progress (a
generation or a succeeded attempt) and when its oldest ready unit was queued,
so an idle stage given new work is not a stall. A re-queue moves a unit's
queue time, so a stage whose ready units upstream churn keeps re-queuing stays
silent however long it has not progressed. `project/year` units have their
own clock, so progress elsewhere in `project` does not hide a stalled year. A
link runs once its event, resolution and years are current: a link with one of
them queued waits for it, and any other link became runnable at its queue time
or when the newest of them was made, whichever is later.
The budgets and the interval come from `config/`, not overrides; a sweep day's
larger points budget is not read.

The checks live in `nix/watchdog.py`, a standard-library script with offline
tests in `tests/test_watchdog.py`, which also run every query against the
real migrations. The unit passes it the timer and backup unit states and the
disk quota. It sends each alert to the journal and `alertCommand` (60-second
limit) first, then stamps and appends it to `ALERTS.log`, so a full disk
cannot silence it, and keeps the log to its last 1 MiB.

## Pause, resume, source switches, and stopping

```sh
sudo -u swingset swingset pause --all
sudo -u swingset swingset pause --host scoring.dance --reason 'operator request'
sudo -u swingset swingset pause --source eepro --until 2026-10-01T00:00:00Z
sudo -u swingset swingset pause --kind round_observations --reason 'review parser' --wait 60
sudo -u swingset swingset resume --all
```

With H13, a pause persists at the next bounded admission boundary while loop
ticks keep their locks. The receipt reports `pausing` while matching
admitted work and leased parse, project and link units drain, then `paused`.
A unit has no admission row; a lease shows as `lease:<stage>/<kind>/<id>`
with its `lease_until`. A lease drains until it expires; an expired one is
listed under `dead_leases` and does not hold the wait (D-0238). `--wait`
waits for that drain; a wait timeout retains the
persisted pause. A control-servicing timeout means no change was
persisted. Doctor reports the pause ID, reason, expiry and exact resume selector.
A worker write that runs 60 seconds is cut off and rolls back; no unit gets
longer. Writes are small by construction, so a firing is an alarm
(`[write-deadline]`, [D-0217](../../journal/decisions/0217-large-generations-store-in-chunks-and-the-write-deadline-is-an-alarm.md)).
The unit retries on the work queue's clock, 15 minutes doubling to six hours,
and is quarantined at the third overrun
([work queue](#work-queue-quarantine-and-requeue)). A control that arrives during such a
write can time out at its default 60 seconds; pass `--lock-timeout 90`.

To stop the scheduled worker immediately:

```sh
sudo systemctl stop swingset-{acquire,plan,derive,release,maintain}.timer
sudo systemctl stop swingset-{acquire,plan,derive,release,maintain}.service
sudo -u swingset swingset pause --all
```

Stopping the timer alone leaves an active tick running. SIGTERM stops
issuing requests and exits at a unit boundary; systemd allows 45 seconds (120
for derive, whose staged write and record take longer). Resume removes
only the selected operator pause. It leaves automatic throttle and block pauses
intact. Each overdue watch is checked once, subject to budget, then returns to
its normal schedule; there is no catch-up burst.

Set a source's `enabled = false` in `config/sources.toml` to disable its fetches.
The same file's top-level `history_start` (2010-01-01) is the date events are
traced from; the build fails on any event that ended earlier, so change it only
as `docs/reference/backfill.md` describes.
Rebuild the service to install config changes. Override CSV changes need no
rebuild: the next tick validates, captures and accepts them transactionally.
Malformed headers, identities, dates, URLs and duplicate policy keys are rejected
before acceptance. Removing an override file invalidates its consumers too.
Disabling fetching or pausing a host permits offline work. Under H13, an all,
source or kind pause also holds matching derivations and shared publication;
unrelated eligible work continues. A valid held candidate is not rejected, and
an in-flight commit must reconcile its receipt before its pause is fully drained.

## Disk space

The worker's disk is bounded by OrbStack at 256 GiB
(`orb config set machine.swingset.disk_bytes 274877906944`), enforced as a
btrfs quota. The kernel's `statfs` ignores btrfs quotas, so plain `df` reports
the host volume instead. Three things show the real number. `swingset-disk`
on the worker prints used, cap and free from the quota group. The worker's
`df` is a wrapper that prints the normal table and then that same line on
standard error. `orb info swingset` on the Mac agrees with both. The watchdog
alerts when less than `services.swingset.diskMinFreeBytes` is free (default
100 GB, the trigger in AGENTS.md). Only root may read the quota, so other
users see the value the watchdog cached in `/run/swingset-disk` within the
last half hour. Inside that bound, three things use most of the space:
the live state, local checkpoints under `/var/lib/swingset/checkpoints`, and
rehearsal scratch under `/var/tmp/swingset-*`. Each rehearsal or checkpoint is
a full copy of the state, several gigabytes, so copies must not accumulate.

- Rehearsal scratch: the `swingset-scratch-clean` timer runs daily at 05:00
  and removes any `/var/tmp/swingset-*` directory in which nothing changed for
  `services.swingset.scratchMaxAgeDays` days (default 3). Put a file named
  `KEEP` at the top of a directory to exempt it while an investigation still
  needs it, and remove the marker when it does not. Retained receipts under
  `journal/evidence/` are the durable record; a scratch copy never is.
- Checkpoints: after it has written one, the backup command prunes timer
  checkpoints (`run_*`) by policy, keeping the newest two and anything under
  two days old by default, and removes abandoned temporary directories after
  a day. Production keeps only the newest while backups are local only
  ([local-only backups](#local-only-backups)). It
  never removes the checkpoint it just wrote, and it logs what it removed and
  kept (`event="checkpoints-pruned"`). Those three numbers are the
  `checkpoint_` values of the `[retention]` table (see
  [state](../reference/state.md#knobs)). Adding `--no-prune` to a backup keeps
  everything for that run. This is cleanup after a backup that already
  succeeded, so a removal that fails is logged as
  `event="checkpoint-prune-failed"` and the backup still reports success; fix
  the directory it names by hand.
- Named checkpoints: a directory whose name is not `run_*` and not a
  `.<name>.tmp-<hex>` copy is an operator's, and policy never removes it, at
  any age, finished or not. A `failed-run_*` directory is a local copy that
  failed its verification ([local-only backups](#local-only-backups)). Remove
  one by name with `swingset backup --remove-checkpoint NAME` once the gate it
  protected has passed; that makes no backup, and it refuses while a restore
  into this state directory is pending. An interrupted `cp -a` leaves a
  directory with no `checkpoint.json`, which nothing can restore from: doctor
  lists it as incomplete, and it stays until you remove it by name.
- A restore reading a checkpoint from this machine into a _different_ state
  directory leaves no mark here, and a `run_*` checkpoint it is reading can be
  pruned under it. Rename that checkpoint before starting such a restore.
- Doctor lists every checkpoint with its name, whether it is complete, its age
  in days, its bytes, and whether policy would prune it, so a stale named
  checkpoint shows up without being hunted for. `prunes` is what policy says at
  that moment; the next backup's own checkpoint takes one of the kept slots, so
  a `run_*` kept as "one of the newest two, until the next backup takes a slot"
  goes at the next backup. Rename it first if you need it.
- The state database: `swingset gc --plan` writes a plan naming every piece of
  saved output and every file, which list it is on, and why, then prints the
  plan's fingerprint and a summary. It removes nothing, and neither does
  `swingset gc` on its own, which prints the same summary and points at the
  commands that act. `swingset gc --apply <fingerprint>` removes what that plan
  named. `swingset gc --reclaim` shrinks the file afterwards.
- When `[state-size]` fires: remove superseded candidates. Every build
  leaves a candidate directory of about 190 MB that nothing prunes; on the
  2026-09-25 copy, 87 of them held 5.9 GB. On the worker use
  `swingset gc --plan --candidates-only`: it lists them as
  `superseded_candidate` (it keeps referenced candidates, the five newest
  others and any a selected build generation names) and walks no graph.
  `swingset gc --apply <fingerprint> --candidates-only` removes what that
  plan named once it is past its age floor (`collect_older_than` after the
  directory's modification time). The plan's `eligibility` and the apply's
  receipt count the files eligible now and the rest with the earliest time
  they can go; an apply that can remove none says so on stderr. A copied or
  restored state directory resets the modification times and so the floor.
  The apply deletes data, so it takes an independent check with a
  written verdict first
  ([removing what a plan named](#removing-what-a-plan-named)). The full
  `gc --plan` walks every generation's dependencies and does not fit the
  8 GB worker (12.1 GB resident on a Mac clone of the 2026-09-25 copy), so
  run it only on a Mac clone of a byte copy. `swingset gc
--collect-closures` frees only proofs of failed builds; a successful
  build's proof stays named. Then compare the growth rate with the phase 1
  post-check receipt and its expected dates for 55 and 64 GB, and ask the
  owner whether invalidation step 3 moves ahead of phase 2
  ([D-0208](../../journal/decisions/0208-keep-the-64-gb-cap-until-closure-collection.md)).
  Nothing else frees space before phase 4, whose storage steps (below) are
  the next step.
- Objects (phase 4): packs and release manifests under `objects/`, about
  0.5 GB on the 2026-09-29 copy. `[objects-size]` fires when `objects/`,
  `blobs/`, `extracts/` and `partitions/` pass 60 GB together. The weekly
  drill needs the database's size, the set's files and 20 GB free under
  `/var/tmp` for a few minutes, and a failed drill keeps its scratch, less
  the restored database, for 20 hours.

To see what is using space: `sudo du -xh -d1 /var/tmp /var/lib/swingset | sort -h`.

A schema migration that drops a table frees pages the same way an apply does,
and leaves the file the size it was. Schema 30 does exactly that. Run
`swingset gc --reclaim` after the switch that migrates, or the database keeps
its old size on disk and in every backup. See
[schema history](../reference/schema-history.md).

### Packing cold output (phase 4)

Phase 4 moves old derivation output, old parse bodies and large old build
recipes out of `state.sqlite` into packs under `objects/`, collects what
nothing names, then shrinks the file
([state: objects and packs](../reference/state.md#objects-and-packs)). The
first run is an operator step under a hold, in the order of the
[architecture plan's phase 4 deploy steps](../plans/architecture.md#9-migration-plan);
after it, maintain does the same in bounded batches each day. Each step
deletes data, so the production run takes an independent check first.

```sh
sudo touch /var/lib/swingset/operator-hold   # and wait until no swingset-* unit is active
sudo -u swingset swingset storage plan
sudo -u swingset swingset storage apply <digest>
sudo -u swingset swingset gc --collect --plan
sudo -u swingset swingset gc --collect --apply <digest>
sudo -u swingset swingset gc --reclaim
```

- `storage plan` writes `gc/plans/storage-<digest>.json`: generations to
  evict, references kept, packs to write, parse and recipe bodies to pack,
  and an estimate of bytes. It removes nothing. On the 2026-09-29 copy it
  took 15 minutes on the Mac at 512 MB.
- `storage apply` refuses without `operator-hold`, during a publication,
  when the digest moved (plan again), or when the disk, quota included, has
  less than twice the kept references' bytes plus 10 GB (its receipt's
  `room`). It writes and verifies every pack first (a damaged one is written
  again from the file), then moves the references in one long transaction
  with `secure_delete` off, and truncates the WAL after. Killed at any point,
  it rolls back to the last commit, and the same digest resumes. A finished
  digest prints its receipt. On the Mac it took 36 minutes at 1.04 GB. With
  `secure_delete` on, the first rehearsal's move wrote a 30 GB WAL (W2).
- `gc --collect` names and removes unnamed objects, partition artifacts and
  dependency sets: 4 minutes on the Mac, 0.10 GB.
- `gc --reclaim` needs `2.5 × bytes in use + 12 GB` free on the quota and
  refuses before it starts without it: about 100 to 112 GB for the 36 to
  40 GB production should hold after collection. The rehearsal used 76 GB
  for 32 GB in use (W3). A quota reading over an hour old is refused: run
  `sudo swingset-disk` first.
- Run `storage plan`, `storage apply`, `gc --collect` and `gc --reclaim` as
  `swingset` (`sudo -u swingset`); run as root they leave root-owned files
  that have broken backups before.
- Receipts land in `gc/receipts/storage-<digest>.json`,
  `collect-<digest>.json` and the reclaim's own; keep one compact copy each
  under `journal/evidence/`.

A revocation reads the chain back to the previous accepted generation,
which is never packed, and nothing in the code revokes today. Should one meet
a packed body, it raises `KeyError` and rolls back;
`body_packs.check_revocation` names the generation, and `swingset storage
restore-body <generation_id>` writes it back. Phase 3 code cannot read a
state after `storage apply`: the way back is the byte copy taken under the
hold.

### Retention plans and holds

```sh
# On a Mac clone: the walk needs more memory than the 8 GB worker has.
uv run swingset gc --plan --state ~/swingset-offload/<clone>
uv run swingset doctor --json --retention --state ~/swingset-offload/<clone> | jq .retention
```

Neither fits the worker: the full plan peaked at 12.1 GB and the retention
walk at 13 GB on the 35 GB copy. On the worker use
`gc --plan --candidates-only`, and `doctor` without `--retention`, which
reports the file's bytes only. The plan lands in `<state>/gc/plans/<fingerprint>.json`, named by its
own fingerprint, and the same state always produces the same file. Plans are not
copied into backups. A plan names every artifact file, so the newest ten are
kept and older ones go as new plans are written.

Doctor's `retention` section shows bytes in use, the file size on disk, the
write-ahead log, free-list bytes, usage against `retention.max_database_bytes`
and `retention.recent_window`, and why the largest local generations stay. Its
`unknown` counts say how many items have no owner and `unknown_items` says which
ones they are, so a payload digest or a path can be looked at without writing a
plan out.
Deleting rows frees pages to SQLite's free list without shrinking the file, so
bytes in use and file size are reported separately.

If the file goes over the cap, the worker sets an operator pause on everything
with the reason "state database file is over its retention size cap" at the
start of the next derive tick. Reading, doctor, controls, `gc`, backup and restore
keep working; fetching and derivation wait.

Resuming on its own pauses again on the next derive tick, because the file is still
over the cap. Either shrink it with apply and reclaim below, or raise
`max_database_bytes` in the `[retention]` table of `config/sources.toml` (64 GB
since [D-0196](../../journal/decisions/0196-raise-the-database-size-cap-to-64-gb.md)) and deploy. Then resume with
`swingset resume --all --reason "..."`.

Adding or changing that table recomputes nothing. The next tick captures a new
input bundle, which records the limits in force as values under
`policy/retention.json`, and accepts it; no stage's recipe reads either the file
or the values, so no work is queued and no generation is rebuilt
([D-0156](../../journal/decisions/0156-capture-the-retention-limits-as-values.md)).

### Removing what a plan named

```sh
uv run swingset gc --plan --state /var/lib/swingset
uv run swingset gc --apply <fingerprint> --state /var/lib/swingset
uv run swingset gc --reclaim --state /var/lib/swingset
```

On the worker, plan and apply candidate directories only; the full plan runs
on a Mac clone of a byte copy:

```sh
uv run swingset gc --plan --candidates-only --state /var/lib/swingset
uv run swingset gc --apply <fingerprint> --candidates-only --state /var/lib/swingset
```

`swingset gc` with no flag prints the same summary and removes nothing. It does
not print a fingerprint to apply, because nothing goes without a plan file to
read first: `gc --plan` writes the file and prints the apply line next to it.

Apply takes the writer lock and then the control lock, works the plan out again
while holding both, and stops if the fingerprint has moved: any hold, candidate
or pointer that appeared since the plan was written makes it refuse and change
nothing. Plan again and read the new plan before applying it. A hold asked for
while apply is running waits, and then sees a state apply has finished with.

If an apply is killed after its note commits and before its files go, run
`gc --plan` again and apply the new fingerprint. That apply finishes the files
the new plan still calls removable and leaves anything the new plan now keeps,
which its receipt lists under `skipped_files`. Nothing is removed on the word of
the killed run alone.

Apply removes only files, and only unreferenced candidate directories past
`retention.collect_older_than`; saved output leaves the file through
[packing](#packing-cold-output-phase-4) instead. It never removes saved output, and it never
removes a file nothing declares: doctor reports those as unknown and a person
decides what they are.

Reclaim needs `2.5 × bytes in use + 12 GB` free (bytes in use excludes the
free list) on the smaller of the state volume and the disk quota, and refuses
before touching anything if it is not there; the phase 4 rehearsals used
2.35 times (WAL included) and, after the fixes, about 1.95 times. SQLite's temporary files go under `/var/lib/swingset/tmp`, not `/tmp`. It holds both locks for the
whole rewrite, which on a large database is minutes. A kill during it rolls back
to the file as it was.

Receipts land in `/var/lib/swingset/gc/receipts/`. An apply receipt is named by
the plan fingerprint and carries the files it planned, the files it removed, any
it found already gone, the payloads it planned (none today) and their bytes, any
earlier unfinished apply it finished with what that apply removed and skipped,
and the plan's totals. Every planned file is in exactly one of those lists. A reclaim receipt is named by when it ran and carries bytes in
use, file size, write-ahead log size and free-list bytes, before and after.
Receipts are not copied into backups; the note in the database is. Keep one small
copy per production apply and reclaim under `journal/evidence/`.

Rerunning apply with a fingerprint it already applied prints the recorded receipt
and changes nothing, so a retry after a lost connection is safe. It also writes
the receipt file again if it is missing, which is how a restored backup, which
leaves `gc/` out, gets its copy back.

### Holds

A hold keeps named computations and files in the live database, so they can be
restored with no network:

```sh
uv run swingset hold add --state /var/lib/swingset \
  --generation dg_abc123 --who sandile --why "checking the 2019 rounds"
uv run swingset hold list --state /var/lib/swingset
uv run swingset hold remove --state /var/lib/swingset --hold-id hold_0f3c...
```

`hold add` walks everything the named computations were built from and refuses
if any of it is not local, listing the ids. An evicted generation whose pack is
on disk counts as local; nothing can bring back one that is neither. A held artifact digest has to be a file under `blobs/` or
`extracts/` already. It writes nothing when it refuses. Holds live one file per
hold under `/var/lib/swingset/holds/`; a hold and the files it names are
included in backups, and holds are taken under the control lock, so one can be
placed while loops run. Add `--artifact <sha256>` to hold a stored page or
extract.

Before stopping the VM, stop the loop and backup timers and services, then use
`orb stop swingset` on the Mac. Start it with `orb start swingset`; inspect logs
and `doctor` before resuming collection.

A maintenance hold is a runtime interlock for one named review, never a
standing state. The marker file says who set it, why, and the date by which
it is reviewed. `doctor` reports it as `operator_hold` with its age and marks
it `overdue` after 48 hours; an overdue hold is the first line of
[status](../status.md) ([D-0176](../../journal/decisions/0176-resume-automatic-collection-and-give-the-hold-an-owner-and-expiry.md)).
Create the marker before stopping the services:

```sh
printf 'who: %s\nwhy: %s\nreview-by: %s\n' sandile 'checking the 2019 rounds' 2026-09-22 |
  sudo tee /var/lib/swingset/operator-hold >/dev/null
sudo systemctl stop swingset-{acquire,plan,derive,release,maintain}.timer swingset-backup.timer swingset-summary.timer
sudo systemctl stop swingset-{acquire,plan,derive,release,maintain}.service swingset-backup.service swingset-summary.service
```

All of these service units require that `operator-hold` be absent before starting,
including their environment setup. The marker is relative to the configured
`services.swingset.stateDir`; substitute that path if it differs. NixOS activation
leaves the marker in place. Timers may become active after activation or reboot,
but their service starts are skipped while the marker exists. This does not stop
a unit already running: wait for the explicit service stops above to finish.

When first installing this guard, bridge activation with runtime drop-ins after
creating the marker and stopping the units above:

```sh
for unit in swingset-{acquire,plan,derive,release,maintain} swingset-backup swingset-summary; do
  sudo install -d "/run/systemd/system/$unit.service.d"
  printf '%s\n' '[Unit]' 'ConditionPathExists=!/var/lib/swingset/operator-hold' |
    sudo tee "/run/systemd/system/$unit.service.d/v2-hold.conf" >/dev/null
done
sudo systemctl daemon-reload
sudo systemctl cat swingset-{acquire,plan,derive,release,maintain}.service swingset-backup.service swingset-summary.service
```

Verify every effective unit show the negative condition before activation.
On this host, runtime masks in `/run/systemd/system` did not override the units
in `/etc/systemd/system`; the services remained loaded. Runtime drop-ins supply
the condition to those loaded units. These drop-ins disappear on reboot, so
complete the declarative activation and verify the generated units contain the
guard before rebooting. Do not edit the generated `/etc/systemd/system`
directory. After that verification, the temporary `v2-hold.conf` drop-ins can be
removed and systemd reloaded while the persistent marker remains in place.

Doctor and explicitly invoked local commands remain available while held. The
marker guards these systemd services; it does not authorize manual publication or
remote backup. After the maintenance review permits normal operation, remove the
marker, clear any runtime masks, and start the timers:

```sh
sudo rm /var/lib/swingset/operator-hold
sudo systemctl unmask --runtime swingset-{acquire,plan,derive,release,maintain}.{service,timer} swingset-backup.{service,timer} swingset-summary.{service,timer}
sudo systemctl start swingset-{acquire,plan,derive,release,maintain}.timer swingset-backup.timer swingset-summary.timer
```

### Rolling back a migration

A switch that migrates the database (phase 1 moves it to schema 33, phase 2
to 34, phases 3 and 4 to 36, phase 5a to 37) is rolled back to the previous
system generation plus the byte copy its preflight took; the older code
refuses the newer schema. Migration 37 deletes the unit admissions and the
requirement history, so after it the byte copy is the only way back; work
since the hold is lost and re-fetched. SQLite replays any
valid write-ahead log it finds beside the file, so a newer
`state.sqlite-wal` left next to the restored copy corrupts it. After a crash
or a killed unit that log can hold committed frames. So:

1. Write `operator-hold` with owner, reason and review date, and stop the
   timers and units, the watchdog's too:

   ```sh
   sudo systemctl stop swingset-{acquire,plan,derive,release,maintain}.timer swingset-backup.timer swingset-summary.timer swingset-watchdog.timer
   sudo systemctl stop swingset-{acquire,plan,derive,release,maintain}.service swingset-backup.service swingset-summary.service swingset-watchdog.service
   ```

   A generation from before phase 5b may also run `swingset-cycle`; stop
   its timer and service too.

2. Switch to the previous generation
   (`sudo /nix/var/nix/profiles/system-<N>-link/bin/switch-to-configuration switch`).
   Stop any timer the switch restarted.
3. Move `state.sqlite`, `state.sqlite-wal` and `state.sqlite-shm` out of the
   state directory, together, into a dated directory beside it. Do not
   delete them; that takes the independent check.
4. Check free space first: `df` of the state directory must exceed the
   preflight file's size plus 10 GB, since the moved-aside newer file
   stays on the same quota. If it does not, move that file to the Mac
   (`/mnt/mac`) first, or stop and ask; a copy that fails partway leaves no
   `state.sqlite` in place. Then copy the preflight's file back as
   `state.sqlite`, owned by `swingset`, and check its sha256 against the
   preflight receipt. Stop if it differs. Phase 1 keeps that copy on the
   Mac, so the copy reads `/mnt/mac`.
5. Move every `run_*` checkpoint written since the switch out of
   `checkpoints/`, so a backup on the old code does not take a newer-schema
   copy as the newest.
6. Check without `doctor`, which does not fit the worker at this size:
   the restored file's schema is the old code's (32 for phase 1, 33 for
   phase 2, 34 for phases 3 and 4, 36 for phase 5a),

   ```sh
   sudo -u swingset /var/lib/swingset/venv/bin/python -c "import sqlite3; print(sqlite3.connect('file:/var/lib/swingset/state.sqlite?mode=ro', uri=True).execute('PRAGMA user_version').fetchone())"
   ```

   and `systemctl is-active` shows every timer and unit stopped. Then remove
   the hold, start the timers as above and `swingset-watchdog.timer`, and
   check the first derive tick's record in `runs/` (`failed=false`, no
   `schema` error; a pre-5b generation may run the cycle instead). Run `doctor` only on a Mac clone. Record each step's
   result in one receipt.

Since phase 5b no option turns the loops off. Going back to the cycle is the
previous system generation only if 5b deployed after 5a on its own; this
tree holds both, so after its migration to 37 the only way back is the byte
copy ([rolling back a migration](#rolling-back-a-migration),
[D-0239](../../journal/decisions/0239-the-loops-are-the-only-orchestration.md)).
Short of a rollback, `[release] path` switches the release assembly
([release path](#release-path)); it re-queues nothing.

## Output cutoff

A parse or derivation whose output is identical keeps its generation and
records an equivalence
([D-0205](../../journal/decisions/0205-cut-off-identical-derivation-output.md)).
`[derivation] cutoff = false` in `config/sources.toml` turns that off for loop
work, `run_intake` and `accept-year`: each re-run mints a new generation.
Paths that read no config keep cutoff on: the offline evidence imports and
`review_pack`, which only the research tool `intake_phase1.py` calls. It stops
new cutoffs only. Recorded equivalences stay valid, so a wrong one (a missed
witness, a stale kept id) stays current and is never re-minted by the switch
alone.

To re-mint after a bad cutoff, set `cutoff = false` and deploy with a byte
change inside the affected code scope, for example a comment in
`versions.py` for every project and link unit (`loops/` is in no scope and
re-runs nothing). The new fingerprints have
no equivalences, so every unit in the scope re-runs and mints. Turn cutoff back
on in a later deploy.

That costs what a deploy cost before phase 1. For every project and link unit it is
about 35,800 project and link generations and about 1.5 GB (1.3 GB of project
and 0.25 GB of link rows on the Mac copy), 4 to 9 hours of CPU, and links
withheld until the link drain ends (the phase 1 receipt, `r2_replay.A`).
Narrow it where you can: a comment inside the affected kind's projector
module re-mints only that kind when its scope differs from the others. The
project scopes still share their storage and projection modules, so most such
comments still reach every project and link unit
([F4](../plans/architecture.md#9-migration-plan)).

## Release path

`[release] path` in `config/sources.toml` picks how the next release is
assembled
([D-0224](../../journal/decisions/0224-releases-from-partition-artifacts.md)):

| Value                   | Use                                                                                                                                                        |
| ----------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `partitioned` (default) | Normal releases, from partition artifacts and a release manifest, one partition at a time                                                                  |
| `whole`                 | A streamed-assembly bug: the same artifacts and rows, assembled in memory. A fallback only if its measured peak fits release's cap with margin (see below) |
| `closure`               | Not a fallback after the phase 3 switch: it built a release with no events on the rehearsal clone (F30); the gate refuses it                               |

An artifact bug has no config fallback: roll back by the byte copy
([rolling back a migration](#rolling-back-a-migration)). `whole` peaked at
3.44 GB on the Mac after F26's fix (the phase 3 re-rehearsal, R4), which fits
release's `MemoryMax = 7G` under the heavy lock; check its first peak on the
worker (`peak_rss_bytes` in the release record) before relying on it.

`config/sources.toml` is read from the Nix store, so a change needs a
`nixos-rebuild switch`. Production reads `overrides/` from
`services.swingset.overridesDir`, which `nix/hosts/orb.nix` points at the
Mac checkout itself: a row written there goes live at the next capture, with
no deploy. Only the owner writes and commits `release_decreases.csv` rows
([approving a decrease](data-corrections.md#approving-a-decrease)). The path
is not a derivation input, so it re-queues nothing; the next build at the
build interval takes it (to build at once, see
[a second build by hand](#a-second-build-by-hand)), and a new path never
reuses a candidate built by another. Set it back after one release. `[release] streamed_lock` (default true) keeps the heavy lock on a
partitioned build ([commands and logs](#commands-and-logs)).

An event partition is published only from generations that agree: its
event, resolution and link must be current, else it keeps the artifact it
last built (`kept_not_current` in the build report counts those, and the row
records `kept_not_current_since`), or is withheld as `units_not_current` when
it has none. `[partitions-stale]` names partitions kept so for over 48
hours. A partition that fails a builder check once assembled (after identity
policy and suppression) is withheld as `assembly_check:<check>`, counted in
`release_policy.partitions` and named in the release record's
`partitions.assembly_withheld`; its `release_partitions` row and the release
manifest still say `built`, since they hold stage 1's verdicts. One whose
selection or stage 1 raises is withheld as `partition_error` with the
error's class and message. A damaged artifact (a file missing, or bytes not
those its sidecar records) is rebuilt by the next stage 1. None of these
stops the release.

### A second build by hand

`swingset release` is a loop tick: within the build interval it defers
(`build_deferred: interval`). To build again at once, for example to check
that unchanged inputs give the same manifest digest, write `operator-hold`,
wait until `systemctl list-units 'swingset-*'` shows no active unit, run
`sudo -u swingset swingset build --overrides <overridesDir>` (it takes no
heavy lock, so nothing else may run beside it), then remove the hold. Pass
the services' `services.swingset.overridesDir` (on the worker the live Mac
checkout, `/Users/skeswa/repos/skeswa/swingset/overrides`): without it the
command reads the deploy tree's copy, and if the two differ the build
accepts other inputs, which re-queues work and changes the gate verdict and
manifest digest. It builds a candidate and
publishes nothing; read its manifest digest and stage 1 counts with
`swingset partitions --withheld` and the candidate's `_meta/manifest.json`.

Each partitioned build reports its manifest in the candidate's
`_meta/manifest.json` (`release_policy.manifest_digest`, `withheld`,
`partitions`, which counts joins closed because their dancer is not in the
release). The release record's `partitions` key adds `assembly_seconds` and
`assembly_peak_rows`. `swingset partitions --withheld` counts what it left
out by reason and lists each partition with its detail
([build: partition artifacts](../reference/build.md#partition-artifacts)).
`[partitions-withheld]` reports the count daily.

`swingset verify-derivations --sample 0.01 --kinds link,year` re-derives this
week's sample of current link and year units, rolls every write back, and
exits 1 when one differs from its generation. The maintain loop runs it
weekly ([verify sample](../reference/operations.md#verify-sample)).

## Work queue: quarantine and requeue

A unit that fails three times in a row with one class on one input is
quarantined for seven days; `unit_io_error` quarantines at twenty. A blocked
parse or a missing artifact waits for input, a day at first and at most a
week, and is then retried. A new input retries a unit at once
([state: work queue](../reference/state.md#work-queue)). A tick that dies
inside a unit, its admission included, counts as that unit's failure. A
tick killed after systemd asked it to stop (a deploy that outlasts
`TimeoutStopSec`) leaves a `runs/<run>.stop` marker and counts nothing. The
tick writes the marker only for a SIGTERM within its budget or under
`operator-hold`: one past its budget with no hold is its start timeout, and
the overrun unit's death counts.

```sh
uv run swingset queue --state ./tmp/state                  # counts, quarantined units
uv run swingset queue --queue-state waiting_input --json --state ./tmp/state
uv run swingset queue requeue --error-class unit_exception:KeyError --by "<who>" --state ./tmp/state
uv run swingset queue requeue --unit project/event/<id> --by "<who>" --state ./tmp/state
```

The report opens read-only. `requeue` needs `--by` and one of
`--error-class` or `--unit`; `--stage` and `--kind` narrow it. It sets the
matching quarantined or waiting rows `ready` with no failures counted and
requests a retry, in one short write that takes no process lock. Deploy the
fix first, or the unit quarantines again after three more failures.

## Reparse and registry work

```sh
uv run swingset reparse --kind wsdc_calendar.events --state ./tmp/state
uv run swingset parse --state ./tmp/state
uv run swingset project --state ./tmp/state
uv run swingset link --state ./tmp/state
uv run swingset build --state ./tmp/state
uv run swingset registry-crosscheck dump.json --archive-only --state ./tmp/state
uv run swingset sweep --start 1 --state ./tmp/state
uv run swingset registry-crosscheck dump.json --state ./tmp/state
uv run swingset registry-crosscheck --blob <sha256> --state ./tmp/state
```

Manual stage commands capture the same input bundle and use the same durable
queues as the loops. A handled parse failure keeps the previous good observations,
records evidence, and completes the failed attempt. A version bump or `reparse`
can retry it. A dump cross-check produces findings; it never imports dump rows
into canonical tables. Before seeding a sweep, archive the known comparison dump
with `--archive-only`; this validates and saves it without comparing an incomplete
mirror. Enable the registry source and seed the sweep. Derive ticks compare the
saved dump after the final batch has been parsed and projected. A durable due
marker survives interruption and is cleared only after comparison succeeds.

EEPro's operator conversation must be recorded in its playbook before live
fetching. Do not reinterpret an unknown registry response as a missing dancer.
WDR's unknown cell meanings remain raw evidence, with warnings where needed.

When inspecting a verified sealed SQLite checkpoint directly, use
`mode=ro&immutable=1` so SQLite does not create WAL or shared-memory sidecars
outside its manifest. Do not use immutable mode for a live or changing database.
An ordinary read-only connection must still read committed WAL contents there.
See [the inspection decision](../../journal/decisions/0102-read-sealed-checkpoints-without-sqlite-sidecars.md).
