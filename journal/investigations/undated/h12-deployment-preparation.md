# Preparing work-isolation migration checks (H12)

> Historical record. Dates, status claims, and instructions below describe the
> original work. See [current status](../../../docs/status.md) before acting.
> [Project journal](../../README.md)

Work isolation lets one task fail without stopping unrelated tasks. This record concerns retries, saved artifacts, or migration checks. The original work ID is H12.

`accept_h12.py` is a separately reviewed operations helper. It is outside the
frozen runtime and has only run against synthetic offline states. The coordinator
owns deployment and execution. The helper performs no cycles, source requests,
policy activation, input acceptance, or publication.

The reviewed runtime is
`/nix/store/0r3giz9x42hmwv448wzg8s2w1z47lwmc-source`. Its `h12-source.json`
SHA256 is `ad1e0b8395b28559e6f4623459955b31574637503edddd994354520b384450ee`.
The helper rejects another source receipt, a runtime whose schema is not exactly
11, or imports outside this source. It reuses the frozen H11 helper for systemd
hold checks, manifest verification, and stable table digests. Future schema-12
working code cannot be used for this operation.

## Gate and evidence

After the schema-10 H11 checkpoint has been uploaded and its private remote
commit verified, copy the reviewed driver into the operations directory and
record its SHA256. Retain a gate next to the backup receipt and verification
artifacts. This is the gate shape; `ACTUAL_*` fields require the recorded values:

```json
{
  "format": "h12-operational-gate-v1",
  "v4_verified": true,
  "v4_commit": "81121dcc7d62a1db9e76ee1c49a6b90e1f4c5653",
  "v4_manifest_sha256": "ACTUAL_PUBLIC_MANIFEST_SHA256",
  "private_backup_verified": true,
  "private_backup_commit": "ACTUAL_VERIFIED_PRIVATE_COMMIT",
  "private_backup_receipt": "backup.json",
  "checkpoint": "/var/lib/swingset/checkpoints/h11-before-h12-20260913",
  "checkpoint_manifest_sha256": "ACTUAL_CHECKPOINT_MANIFEST_SHA256",
  "source_receipt_sha256": "ad1e0b8395b28559e6f4623459955b31574637503edddd994354520b384450ee",
  "driver_sha256": "ACTUAL_REVIEWED_OPERATIONS_DRIVER_SHA256",
  "verified_at": "ACTUAL_VERIFICATION_TIME",
  "evidence_files": {
    "backup.json": "ACTUAL_BACKUP_RECEIPT_SHA256",
    "private-verification.json": "ACTUAL_RETAINED_VERIFICATION_SHA256",
    "v4-verification.json": "ACTUAL_RETAINED_V4_VERIFICATION_SHA256"
  }
}
```

Evidence paths stay inside the gate directory. The actual `backup.json` format
contains `commit`, `manifest_hash`, `checkpoint`, `files`, `schema_version: 10`,
`baseline_commit`, `finished_at`, and `source_receipt_sha256`. The driver checks
these against the gate, checkpoint, and frozen source. The gate attests to the
coordinator's completed remote verification; an offline helper cannot establish
remote truth by itself.

Checkpoint format-1 file records contain integer `size` and `sha256`. The driver
checks the complete file set and both values, then reads checkpoint SQLite with
`mode=ro&immutable=1` and closes it explicitly. Extra WAL/SHM files fail the check.
Live state must match the checkpoint in every existing user table; only
`last_backup*` meta bookkeeping may differ. Admission, identity, H11 cohorts,
requirements, controls, watches, snapshots, and pending work are included in the
content hashes. The current baseline's manifest and every declared file are
verified before and after execution.

## Commands

Preserve the VM's existing library environment. The source root is included in
`PYTHONPATH` so the helper imports its frozen `research.accept_h11` dependency.
The output paths must be new and outside source, checkpoint, candidate, and
baseline trees; `/var/lib/swingset/operations/h12-20260913` is an appropriate
operations directory.

```sh
PYTHONDONTWRITEBYTECODE=1 \
PYTHONPATH=/nix/store/0r3giz9x42hmwv448wzg8s2w1z47lwmc-source/src:/nix/store/0r3giz9x42hmwv448wzg8s2w1z47lwmc-source \
  /var/lib/swingset/venv/bin/python \
  /var/lib/swingset/operations/h12-20260913/accept_h12.py \
  --source /nix/store/0r3giz9x42hmwv448wzg8s2w1z47lwmc-source \
  --state /var/lib/swingset \
  --gate /var/lib/swingset/operations/h12-20260913/gate.json \
  --output /var/lib/swingset/operations/h12-20260913/preflight.json
```

The default only reads state and writes its new preflight receipt. After reviewing
that receipt, the coordinator repeats the command with `--execute` and a new
`execution.json` output. Execution holds the normal process writer lock across
preflight, migration, doctor, and fresh-process verification. The existing
scheduled-service hold must remain present, and all cycle/backup/summary services
and timers must be stopped with their exact negative hold conditions. The helper
never changes those controls.

Migration 0011 seeds one `work_generations` row for each existing pending unit,
with generation 1, retry generation 0, and no retry request. `work_attempts` must
remain empty. All prior table contents must remain byte-for-byte equivalent under
stable row serialization, except the schema-version meta value. Doctor must give
the same JSON in a fresh process at a fixed comparison time, its human requirement
report must contain the same fields, and neither read may change database rows.
No H11 scan or new cohort is needed.

The default total bound is 900 seconds; `--max-seconds` accepts 1–1800. A failure
after migration begins leaves a failed receipt and keeps the hold in place. A
successful migration is not rolled back if a later acceptance check fails; the
coordinator reviews that receipt before taking any next step. Re-execution against
an already migrated schema-11 database is rejected. Successful output records
`passed: true`, schema 11, unchanged baseline and protected tables, empty attempts,
exact queue-generation seeding, and matching doctor reads.
