# Operating Swingset

Use this guide to inspect the worker, manage pauses, and request local work.
Read [current status](../status.md) before changing production. A token alone
does not enable publication; begin a new installation with `dryRun = true`.
The [current extension handoff](../../journal/investigations/2026/event-extension-operating-handoff-2026-09-17.md)
pins the held runtime, checkpoint and unfinished activation checks.

For other tasks, see [installation](deployment.md),
[backup and restore](backup-and-restore.md), or [data corrections](data-corrections.md).
The [operations reference](../reference/operations.md) owns worker behavior.

## Commands and logs

Data-writing commands use the same state lock. H13 pause and resume commands,
and the `hold` commands in [disk space](#disk-space), use the separate bounded
control path described below. A timer overlap exits 0 with
`skipped-overlap`. Manual mutations wait up to `--lock-timeout` (60 seconds by
default); a timeout exits nonzero and applies no change. Backup waits for the
writer and the service retries failures after 60 seconds.

```sh
sudo -u swingset swingset doctor
sudo -u swingset swingset summary
journalctl -u swingset-cycle --since today
sudo systemctl status swingset-cycle
```

When running the CLI outside the installed service, pass absolute `--config`
and `--overrides` paths if the current directory is not the checkout.
Run state and control commands as the service user. Both `state.lock` and
`control.lock` need that user's read/write access. If a retained lock has the
wrong owner, stop ordinary jobs and inspect it before repair; never delete or
replace a lock file to fix permissions. See the
[H16 ownership repair](../../journal/decisions/0033-repair-production-control-lock-ownership.md).
`doctor` reads without taking the writer lock. `runs/*.json` holds per-cycle
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

Optional `[scheduling]` settings in `config/sources.toml` tune cycle shares and
pressure thresholds under the [scheduling contract](../reference/scheduling.md).
Defaults reserve 30 seconds for reconciliation, 300 for acquisition and 390 for
offline work in a 720-second cycle. Builds use offline time before collection
borrows it. Host budgets and request floors remain independently enforced.

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

H10 adds public identity acceptance metadata before schema 1.0. Until H17 has
approved expansion, defaults may only retain supported IDs from the published
baseline. Judges without WSDC numbers remain named records with null IDs.

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
`skeswa/swingset-archive` (private dataset). Provision a write token for both in
a root-owned mode-0600 file outside the checkout, for example
`/etc/swingset.env`, containing `HF_TOKEN=...`. Never commit that file or copy it
into a state checkpoint.

Set `services.swingset.environmentFile = "/etc/swingset.env";` and rebuild.
After reviewing the first dry build, set `services.swingset.dryRun = false;`
and rebuild again. An existing public dataset requires a matching private
checkpoint; bootstrapping accepts only `.gitattributes` and the generated ODC-By
license-only README. It refuses arbitrary existing content.

For rotation, stop the cycle and backup services, replace the environment file,
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
  backup releases `state.lock`, so cycles do not wait; the prune takes the
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
   unchanged after the first cycle.

A local backup briefly holds two checkpoints, so free space dips by about
36 GB during each one; while the backup unit runs, `[disk-low]` fires only
below the 20 GB headroom its room check kept. As the state grows,
`[disk-low]` stays on past a state size of about 59 GB and the room check
refuses past about 66 GB (rough estimates). Nothing alarms before then, so
each post-check receipt records the state size without `checkpoints/`
against the 55 GB phase 4 trigger in the
[architecture plan](../plans/architecture.md).

## Alerts

`swingset-watchdog` runs every 30 minutes and after any cycle or backup
failure. It appends to `/var/lib/swingset/ALERTS.log`, logs at error level,
and pipes to `services.swingset.alertCommand` when set, for example
`curl -s -d @- https://ntfy.sh/<topic>`; the alert text arrives on standard
input. Read the log with `journalctl -t swingset-watchdog`
([D-0188](../../journal/decisions/0188-alert-on-failing-or-stalled-cycles-and-stop-re-queuing-on-config-switches.md),
[D-0202](../../journal/decisions/0202-the-watchdog-alarms-on-progress-within-two-hours.md)).

Each line starts with a key. A key already logged within
`watchdogRepeatSeconds` (two hours) is not sent again. For the gate keys,
`[host-failing]`, `[backup-failed]` and `[watchdog-blind]` only the same text
is held back, so a newly blocked host repeats at once. With `alertCommand`
set, the unit also sends one `[watchdog-alive]` line a day to it, not to
`ALERTS.log`; a missing one means the watchdog timer or the notifier stopped.

| Key                      | Fires when                                                                                                                                                                                | Held |
| ------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---- |
| `[watchdog-blind]`       | a check could not run twice in a row, a second apart (a missing table, a bad stamp); names each one. The other checks still run                                                           | yes  |
| `[watchdog-error]`       | `watchdog.py` crashed or exited nonzero; the wrapper writes it, at most once per `watchdogRepeatSeconds`, and the script's stderr goes to the journal                                     | yes  |
| `[cycles-failing]`       | the last three cycles failed; records of other commands (backup, gc) do not count here or in `[cycles-quiet]`                                                                             | yes  |
| `[size-cap-pause]`       | the size-cap pause is active ([retention](#retention-plans-and-holds))                                                                                                                    | yes  |
| `[hold-overdue]`         | the `operator-hold` file or an all-scope pause is older than 48 hours, or an all-scope pause has no `created_at` (age unknown); repeats daily                                             | yes  |
| `[timer-inactive]`       | nothing holds the worker and `swingset-cycle.timer` is not active                                                                                                                         | —    |
| `[cycles-quiet]`         | no cycle finished for `watchdogQuietSeconds` (two hours)                                                                                                                                  | no   |
| `[stale-fetch]`          | no request for `staleFetchSeconds` (two hours); names the stored-page age, hosts that spent today's budget and the ready parse backlog, since backpressure holds requests                 | no   |
| `[budgets-spent]`        | in place of `[stale-fetch]` when every host that fetched today has spent its daily request or byte budget; repeats daily                                                                  | no   |
| `[stale-progress]`       | a stage with ready units made no progress for `staleProgressSeconds` (two hours) since its work became runnable; one clock per stage, and one for `project/history`                       | no   |
| `[gate-legacy-spacing]`  | a host's request spacing is unknown, so the gate skips it with no expiry ([D-0190](../../journal/decisions/0190-record-spacing-baselines-for-the-origin-hosts-from-the-eight-day-gap.md)) | yes  |
| `[gate-host-blocked]`    | a host is paused as `Blocked`                                                                                                                                                             | yes  |
| `[host-failing]`         | a host's last four or more responses were all 429 or 5xx; clears on its next success                                                                                                      | yes  |
| `[disk-low]`             | the disk quota runs low ([disk space](#disk-space)); while a backup runs, only below its 20 GB headroom                                                                                   | yes  |
| `[backup-failed]`        | `swingset-backup.service` is failed, or a `failed-run_*` checkpoint exists ([local-only backups](#local-only-backups))                                                                    | yes  |
| `[backup-stale]`         | upload is off and the newest complete `run_*` checkpoint is older than `backupMaxAgeSeconds` (36 hours), or there is none                                                                 | yes  |
| `[backup-upload-paused]` | upload is off; names the newest local checkpoint and its age; repeats at most once a day                                                                                                  | yes  |

"Held" says whether the line still fires while held. Held means the
`operator-hold` file exists or an all-scope operator pause other than the size
cap is active; an inactive cycle timer silences the same lines and raises
`[timer-inactive]` instead. A scoped pause (source, kind or host) is not a
hold: if it holds all ready work, `[stale-progress]` fires, so use an
all-scope pause or `operator-hold`. A gate that skips every request is not a
failure, so only the fetch and progress lines report it. Requests count, not
stored pages: a 304 stores nothing. A ready unit is one whose latest attempt
is absent or succeeded, has a retry requested since, or is transient or
interrupted with its retry time past. A blocked or failed unit is not ready
and raises nothing. A stage's clock is the later of its last progress (a
generation or a succeeded attempt) and when its oldest ready unit was queued,
so an idle stage given new work is not a stall. A re-queue moves a unit's
queue time, so a stage whose ready units upstream churn keeps re-queuing stays
silent however long it has not progressed. `project/history` is paced, so it
has its own clock: from when it came due (its output's time plus
`history_interval_seconds`, or its queue time when it has no output) or its
last success or output, whichever is later. Every link waits for it, so links
are clocked from when history last materialized after their last progress, or
from when it came due and did not run, whichever is earlier. Links that
progressed after history's newest output wait by design until it is due again.
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

With H13, a pause persists at the next bounded admission boundary while the
cycle retains its process lock. The receipt reports `pausing` while matching
admitted work drains, then `paused`. `--wait` waits for that drain; a wait timeout
retains the persisted pause. A control-servicing timeout means no change was
persisted. Doctor reports the pause ID, reason, expiry and exact resume selector.
Worker write phases have a 45-second limit inside the default 60-second control
bound; the history projection alone has 300 seconds. A deadline failure rolls
back output and retries with a doubling delay, from 15 minutes to six hours
(D-0198). Earlier deployed runtimes still wait behind the cycle lock.

To stop the scheduled worker immediately:

```sh
sudo systemctl stop swingset-cycle.timer
sudo systemctl stop swingset-cycle.service
sudo -u swingset swingset pause --all
```

Stopping the timer alone leaves an active cycle running. SIGTERM stops issuing
requests and exits at a unit boundary; systemd allows 45 seconds. Resume removes
only the selected operator pause. It leaves automatic throttle and block pauses
intact. Each overdue watch is checked once, subject to budget, then returns to
its normal schedule; there is no catch-up burst.

Set a source's `enabled = false` in `config/sources.toml` to disable its fetches.
The same file's top-level `history_start` (2010-01-01) is the date events are
traced from; the build fails on any event that ended earlier, so change it only
as `docs/reference/backfill.md` describes.
Rebuild the service to install config changes. Override CSV changes need no
rebuild: the next cycle validates, captures and accepts them transactionally.
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

To see what is using space: `sudo du -xh -d1 /var/tmp /var/lib/swingset | sort -h`.

A schema migration that drops a table frees pages the same way an apply does,
and leaves the file the size it was. Schema 30 does exactly that. Run
`swingset gc --reclaim` after the switch that migrates, or the database keeps
its old size on disk and in every backup. See
[schema history](../reference/schema-history.md).

### Retention plans and holds

```sh
uv run swingset gc --plan --state /var/lib/swingset
uv run swingset doctor --json --state /var/lib/swingset | jq .retention
```

The plan lands in `/var/lib/swingset/gc/plans/<fingerprint>.json`, named by its
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
start of the next cycle. Reading, doctor, controls, `gc`, backup and restore
keep working; fetching and derivation wait.

Resuming on its own pauses again on the next cycle, because the file is still
over the cap. Either shrink it with apply and reclaim below, or raise
`max_database_bytes` in the `[retention]` table of `config/sources.toml` (64 GB
since [D-0196](../../journal/decisions/0196-raise-the-database-size-cap-to-64-gb.md)) and deploy. Then resume with
`swingset resume --all --reason "..."`.

Adding or changing that table recomputes nothing. The next cycle captures a new
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

Apply removes only files today, and only unreferenced candidate directories past
`retention.collect_older_than`. It never removes saved output, and it never
removes a file nothing declares: doctor reports those as unknown and a person
decides what they are.

Reclaim needs about twice the current file size free on the state volume and
refuses before touching anything if it is not there. It holds both locks for the
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
if any of it is not in the live database, listing the ids to bring back first
with `gc --restore`. A held artifact digest has to be a file under `blobs/` or
`extracts/` already. It writes nothing when it refuses. Holds live one file per
hold under `/var/lib/swingset/holds/`; a hold and the files it names are
included in backups, and holds are taken under the control lock, so one can be
placed while a cycle runs. Add `--artifact <sha256>` to hold a stored page or
extract.

Before stopping the VM, stop cycle and backup timers and services, then use
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
sudo systemctl stop swingset-cycle.timer swingset-backup.timer swingset-summary.timer
sudo systemctl stop swingset-cycle.service swingset-backup.service swingset-summary.service
```

All three service units require that `operator-hold` be absent before starting,
including their environment setup. The marker is relative to the configured
`services.swingset.stateDir`; substitute that path if it differs. NixOS activation
leaves the marker in place. Timers may become active after activation or reboot,
but their service starts are skipped while the marker exists. This does not stop
a unit already running: wait for the explicit service stops above to finish.

When first installing this guard, bridge activation with runtime drop-ins after
creating the marker and stopping the units above:

```sh
for unit in swingset-cycle swingset-backup swingset-summary; do
  sudo install -d "/run/systemd/system/$unit.service.d"
  printf '%s\n' '[Unit]' 'ConditionPathExists=!/var/lib/swingset/operator-hold' |
    sudo tee "/run/systemd/system/$unit.service.d/v2-hold.conf" >/dev/null
done
sudo systemctl daemon-reload
sudo systemctl cat swingset-cycle.service swingset-backup.service swingset-summary.service
```

Verify all three effective units show the negative condition before activation.
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
sudo systemctl unmask --runtime swingset-cycle.service swingset-backup.service swingset-summary.service swingset-cycle.timer swingset-backup.timer swingset-summary.timer
sudo systemctl start swingset-cycle.timer swingset-backup.timer swingset-summary.timer
```

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
queues as a cycle. A handled parse failure keeps the previous good observations,
records evidence, and completes the failed attempt. A version bump or `reparse`
can retry it. A dump cross-check produces findings; it never imports dump rows
into canonical tables. Before seeding a sweep, archive the known comparison dump
with `--archive-only`; this validates and saves it without comparing an incomplete
mirror. Enable the registry source and seed the sweep. Normal cycles compare the
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
