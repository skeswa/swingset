# Operating swingset

Start with `dryRun = true`. A token does not enable publication. The
[implementation status](implementation-status.md) distinguishes local verification
from the live acceptance work still required by the plan.

On 2026-09-13 UTC H1/H2 were deployed and the controlled G1 probe passed:
an identical not-found response advanced cursor 29029 to 29030. Its source
pause is cleared. V1 through V4 corrections are published and H16 is deployed.
Scheduled workers remain held by `/var/lib/swingset/operator-hold` during v2
acceptance. The
[v2 operating receipt](v2-progress.md#g1-operating-receipt) records the
verification, active runtime, and recovery checkpoint.

## Development and VM setup

```sh
nix develop
uv sync --frozen
uv run pytest -q
uv run ruff check .
uv run mypy
mise install
mise run fmt
orb create --cpus 4 --memory 8G nixos:25.11 swingset
orb -m swingset
cd /Users/skeswa/repos/skeswa/swingset
sudo nixos-rebuild switch --flake .#orb --impure
systemctl list-timers 'swingset-*'
journalctl -u swingset-cycle -f
```

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
changes still require their respective version bumps. Captured runtime recipes
also detect changed bytes when a manual version stays the same.

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
`doctor` reads without taking the writer lock. `runs/*.json` holds per-cycle
counts, stages, host pauses, errors, and the graceful-stop flag. A hard kill can
lose the final run log; SQLite transactions and already durable artifacts remain
the recovery source.

H14 adds a `scheduler` section to doctor and summary. `initial_objectives`
contains policy targets; `observed_host_usage` retains all actual daily debits,
including requests before H14. `observed_attributed_service` counts requests
issued under the new policy. Offline attempt counts do not establish completed
requirements. Request wall ages include operator pauses.

Optional `[scheduling]` settings in `config/sources.toml` tune cycle shares and
pressure thresholds under the [scheduling contract](../design/scheduling.md).
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
as `design/backfill.md` describes.
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

## Backup, restore, and a remote-ahead report

Disable timers and stop the former writer before activation. Use a fresh machine:

```sh
orb create nixos:25.11 swingset-restore
```

Provision `HF_TOKEN` for remote verification. Select a specific private archive
commit and restore its complete checkpoint:

```sh
swingset restore --archive-commit <commit-sha> --state /var/lib/swingset \
  --writer-stopped
swingset doctor --state /var/lib/swingset
```

An already downloaded checkpoint can be selected with
`--checkpoint /path/to/checkpoint` instead. Restore holds the writer lock
through installation and activation. Existing active state is refused; a
marked partial restore can be retried.

Restore verifies files, hashes, SQLite integrity, derived extracts, captured
inputs, baseline and pending candidate directories. `RESTORE_PENDING` blocks
normal mutations until public-head verification succeeds. Verification reads
the public head even if a local receipt exists, then checks it again before
activation. Restore itself never publishes.

A mismatch report names the expected and actual public SHAs. Leave publication
and timers disabled. Restore a newer matching checkpoint, recover the missing
private state, or make an explicit recovery decision about lost evidence.
Do not reset public history or reconstruct private observations from public
Parquet. A public commit after the checkpoint cannot be recovered automatically
without its private evidence.

Inspect the restored `doctor` output and perform a dry cycle. Resume only one
writer. Disposable environments are rebuilt from the frozen lock. A checkpoint
is successful only after the private archive commit is acknowledged.

## Review and removals

Review published `review_queue` rows and add narrowly scoped alias, identity,
source URL, or suppression CSV rows. Use `NONE` for an explicitly unmatched
identity override. Rebuild and inspect the candidate before enabling publication.
Suppression runs at build and leaves raw private evidence intact. Replace an
affected committed parser fixture with another event and update its expected
observations; do not merely hide the fixture from the tests.

GC is manual (`swingset gc`). It must retain artifacts referenced by snapshots,
findings, accepted inputs, retained candidates, or checkpoints and remove only
unreferenced files older than one day.

## Fresh-machine recovery configuration

The `orb-restore` flake configuration installs the CLI and state owner without
collection, backup, or summary timers. Use it for the recovery drill while the
original writer is stopped:

```sh
orb create --cpus 4 --memory 8G nixos:25.11 swingset-restore
orb -m swingset-restore
cd /Users/skeswa/repos/skeswa/swingset
sudo nixos-rebuild switch --flake .#orb-restore --impure
```

Provision the credential separately, then restore the acknowledged archive
commit using a transient service with `EnvironmentFile=/etc/swingset.env`.
Run doctor and a dry cycle before choosing one writer. The recovery machine
remains without timers unless deliberately configured as the new writer.

## Deployment handoff (2026-09-10 UTC)

The selected writer is the OrbStack machine `swingset`. The
`swingset-restore` machine passed recovery of both the original flat checkpoint
and the packed checkpoint, then was stopped. It has no collection timers.
Keep exactly one writer active. The token stays in `/etc/swingset.env`, owned
by root with mode 0600, outside the checkout and checkpoint.

The initial published calendar head is
`957b9e266b549d729ef978b3d0ddcc738c7d901f`. Recovery evidence is in GitHub issue
#8; the packed archive used was
`c691a760015698247d157c2c2cf738fc712ea53e`. The recovery targets were
`/var/lib/swingset/recovered` and `/var/lib/swingset/recovered-packed`, separate
from the CLI environment and caches. Always pass the selected target with
`--state` to restore, doctor, and the verification dry cycle.

Historical receipt: the initial results publication used installed revision
`8fcc0a2564a635ea78ab69c01d9e2055233b4e23`, candidate
`cand_984c84cc9dd34af8`, and public commit
`a4abf85ff6e6e0ad4dd2088e130668987856613a`. Candidate review checked all 17
schemas, file hashes, provenance, unique keys and dataset-card counts. Public
verification matched the reviewed manifest and a remote DuckDB query returned
3,368 placement rows. The published surface contains 312 contests, 255 events,
12,397 entries and 498 rounds. The Hub reports all 17 splits ready, and events, placements, entries, rounds
and final-marks previews each returned HTTP 200 with 100 rows. The private
results-state backup completed at 15:37:05 UTC at archive commit
`c716bf7f7e8eed8369828c26e2a2434f3c08aaf0`.

The production registry comparison dump is archived under SHA-256
`ae7f2b9d688b69b49d08dfc718f60e5ef6b4b6561054d2503e8c94b8ae5e1d53`, and
sweep seed 1 is installed. Initial collection fetched eight higher-priority
finalist refreshes using the normal five-second floor. Their build exposed a
registry vocabulary mismatch; the repair preserves skill and age categories
and highest-level fields. The full sweep and saved-dump comparison remain
tracked in issue #10.

The host now enables publication (`dryRun = false`). Normal collection is
every 15 minutes with up to 120 seconds of jitter; backup is 04:00 UTC Monday
through Thursday and 04:00/12:00/20:00 UTC Friday through Sunday. Summary runs
at 08:00 UTC. Check GitHub issue #9 for scheduled-run acceptance, and #1 for
overall acceptance. Issues #12–#14 track the remaining source
observations.

The active corrected release is
`2089803d379ef4233ee45a49193ce97befcf0911`. Its reviewed public candidate is
`cand_d06d6d9e48fd41a2` at commit
`ec6b7bbb84be9e5252d55c6bb1eae3feef05dd5b`. The public data has 573 events,
96 with results, 13,922 placements, 57,505 entries, 3,229 dancers and 33,427
registry placements. It includes 1,269 registry event mappings and 9,471
entries with a WSDC ID.

Private backup `556e04b59b8404a3e5a12f91c4804eecbf8fee2f` passed verification:
9,240 files and the 691,200,000-byte transport hash and size matched, as did the
public manifest and `PUBLISHED` receipt. Doctor showed no pending work or
candidates, pauses, or restore marker before resumption. All three review guards
were removed and timers resumed at 2026-09-11 03:29:39 UTC. At handoff the cycle
service was running, while backup and summary were waiting for 04:00 and 08:00
UTC. Do not count the running cycle as completed observation evidence.

The registry mirror remains partial at cursor 3,297 against the archived
27,039-ID dump. Keep issues #10, #12, #13 and #14 open; issue #15 records the
completed correction release. See the
[2026-09-10 data-quality repair report](../research/data-quality-repair-2026-09-10.md).

The prior release `9bade45dd5d0c96264aaf463044df4f4aa4700fa`, public
commit `c9789bad676e64424aa9ab7fc581b9aefe16868c`, candidate
`cand_b2c68aaa3b7b4f3c`, and checkpoint
`245f6a4f507581254c989ee20aa7529f0f506a5b` are historical receipts. Their
cursor-2 and active-timer observations do not describe the current handoff.
