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

Data-writing commands use the same state lock. H13 pause and resume commands
use the separate bounded control path described below. A timer overlap exits 0 with
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
bound. A deadline failure rolls back output and remains visible for review or
explicit retry. Earlier deployed runtimes still wait behind the cycle lock.

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

Before stopping the VM, stop cycle and backup timers and services, then use
`orb stop swingset` on the Mac. Start it with `orb start swingset`; inspect logs
and `doctor` before resuming collection.

For a maintenance hold that survives deployment and reboot, create the persistent
marker before stopping the services:

```sh
sudo touch /var/lib/swingset/operator-hold
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
