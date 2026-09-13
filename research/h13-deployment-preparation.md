# H13 migration and control acceptance

`accept_h13.py` prepares schema 11→12 acceptance for the deployment coordinator.
It has only run on synthetic offline states. The default is read-only preflight;
`--execute` migrates and exercises one bounded local control probe. It does not
start a cycle, request a source, accept input changes, activate acquisition or
publish a dataset.

The frozen source is `/nix/store/mn94ln72schglx9fb1qq8k48bv8nl37a-source`, with 522 files and
`h13-source.json` SHA256
`ad95f42e33c551986af2ddaa0199937e4d4ef7bff9f88817f108f412216d0f7e`.
The command requires that independently reviewed hash, and the gate must match it. The
source receipt must have format `h13-reviewed-source-v1`, schema 12, an exact
`files` hash map, `acquisition_enabled: false`, and `repairs_activated: false`.
The runtime and reused H11 verification helper must import from that source.
An unchanged gate cannot authorize a later working tree or schema 13.

## Prerequisites

The schema-11 backup is
`/var/lib/swingset/checkpoints/h12-before-h13-20260913`. The coordinator recorded
private commit `26038f82a082044c4ca04506a27cf380c7e8c476`, manifest SHA256
`ab2ddef462bf3248cd65b85cfacbbc589502bee3dc60e3d15174717dd9304cdc`, and 65,930
files, with remote privacy, head, and manifest verification. Those receipts live
in `/var/lib/swingset/operations/h13-20260913`; the helper independently verifies
the retained receipt hashes and complete local checkpoint closure.

The backup receipt's `source_receipt_sha256` identifies the **deployed H12** source:
`ad1e0b8395b28559e6f4623459955b31574637503edddd994354520b384450ee`. The gate's
`source_receipt_sha256` identifies the **new H13** source. These have separate roles.
The backup receipt also supplies schema 11, the checkpoint path and manifest,
file count, private commit, V4 baseline commit, and completion time.

The current public baseline must still be
`81121dcc7d62a1db9e76ee1c49a6b90e1f4c5653`. Its manifest and every declared file
are checked before and after execution. Checkpoint records require exact integer
sizes and SHA256 values. Checkpoint SQLite is opened with
`mode=ro&immutable=1`, closed explicitly, and checked for integrity; extra files,
including WAL/SHM sidecars, fail preflight.

The scheduled-service hold stays present, and cycle, backup, and summary services
and timers must be stopped with their inspected negative hold condition. The
helper does not change systemd. It takes the normal writer lock for execution;
restricted control operations remain independent of that lock.

## Gate and commands

Copy the reviewed driver into the operations directory and hash that exact copy.
Retain the following gate beside the actual verification receipts. Replace each
`ACTUAL_*` field with a reviewed value; placeholders are not evidence.

```json
{
  "format": "h13-operational-gate-v1",
  "v4_verified": true,
  "v4_commit": "81121dcc7d62a1db9e76ee1c49a6b90e1f4c5653",
  "v4_manifest_sha256": "ACTUAL_V4_MANIFEST_SHA256",
  "private_backup_verified": true,
  "private_backup_commit": "26038f82a082044c4ca04506a27cf380c7e8c476",
  "private_backup_receipt": "backup.json",
  "checkpoint": "/var/lib/swingset/checkpoints/h12-before-h13-20260913",
  "checkpoint_manifest_sha256": "ab2ddef462bf3248cd65b85cfacbbc589502bee3dc60e3d15174717dd9304cdc",
  "source_receipt_sha256": "ad95f42e33c551986af2ddaa0199937e4d4ef7bff9f88817f108f412216d0f7e",
  "driver_sha256": "ACTUAL_REVIEWED_DRIVER_SHA256",
  "verified_at": "ACTUAL_REMOTE_VERIFICATION_TIME",
  "evidence_files": {
    "backup.json": "ACTUAL_BACKUP_RECEIPT_SHA256",
    "private-verification.json": "ACTUAL_PRIVATE_VERIFICATION_SHA256",
    "v4-verification.json": "ACTUAL_V4_VERIFICATION_SHA256"
  }
}
```

The gate records completed remote verification. This offline helper cannot
establish remote truth by itself.

```sh
PYTHONDONTWRITEBYTECODE=1 \
PYTHONPATH=/nix/store/mn94ln72schglx9fb1qq8k48bv8nl37a-source/src:/nix/store/mn94ln72schglx9fb1qq8k48bv8nl37a-source \
  /var/lib/swingset/venv/bin/python \
  /var/lib/swingset/operations/h13-20260913/accept_h13.py \
  --source /nix/store/mn94ln72schglx9fb1qq8k48bv8nl37a-source \
  --source-receipt-sha256 ad95f42e33c551986af2ddaa0199937e4d4ef7bff9f88817f108f412216d0f7e \
  --state /var/lib/swingset \
  --gate /var/lib/swingset/operations/h13-20260913/gate.json \
  --output /var/lib/swingset/operations/h13-20260913/preflight.json
```

After reviewing preflight, the coordinator repeats the command with `--execute`
and a new `execution.json` output. Preserve the VM's library environment. Outputs
must be new and outside source, baseline, candidate, checkpoint, blob, extract,
and accepted-input roots. Derived doctor reports and the small restore specimen
remain beside the operations receipt.

## What execution proves

Every prior user table is hashed, including existing H12 pending tokens,
generations, attempts, admission state, identity state, watches, budgets, and H11
cohorts. For legacy pauses, the four original fields are compared independently
of the new migration columns. The migration must assign legacy IDs, preserve
reasons and expiry, record revision-0 `legacy_import` events, leave new execution
tables empty, and install publication fences on every required user table.

The probe uses only the reserved host selector `h13-acceptance.invalid`; it never
resolves or contacts that name. A subprocess admits a local `acceptance_probe`
action and waits up to 30 seconds. The coordinator persists a pause through the
restricted control API while that child is active, verifies `pausing`, checks
a lightweight fresh-process read through the same `controls.status` module that
doctor embeds, then immediately lets the child finish. The status must
become `paused`; explicit probe resume must then produce `running` for that
selector. Each control mutation has a five-second servicing bound.

During the pause, the driver captures the actual five control/reservation tables
in memory. After the child finishes and its pause is resumed, it copies those
captured rows into an otherwise empty schema-12 state under the operations output. It uses the
real checkpoint and restore APIs on that small **control-plane specimen**. The
restored rows and active drain must match exactly, and `RESTORE_PENDING` must
prevent control mutation. This is a bounded H13 persistence test, not a full
production restore. No production facts or source artifacts enter the specimen.

Successful execution adds exactly one settled probe admission and two control
events, pause and resume, at revisions 1 and 2. Existing pauses and imported audit
rows remain unchanged. The full doctor report is generated only after the probe finishes and resumes;
the fresh full-doctor process returns a canonical hash instead of a second large
report payload. The JSON/human report is retained once, and doctor reads must not
change any rows. A preexisting
global pause, an occupied probe selector, or a legacy timed pause that could
expire within the maximum 30-minute execution window fails preflight; the driver never clears existing
operator controls to make its test pass.

The total default bound is 900 seconds, configurable from 1 to 1800. A failure
after migration leaves a failed receipt and the scheduled-service hold. If the
probe had paused, its separate held receipt names the action and selector; its
pause remains for explicit coordinator recovery. The child is stopped, and its
unsettled admission remains auditable. The helper never silently resumes a failed
probe or automatically retries against an already migrated database.
