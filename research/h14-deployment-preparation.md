# H14 migration acceptance preparation

`accept_h14.py` is a separately hashed operations driver for schema 12→13. Default
execution is read-only preflight. Only the deployment coordinator runs
`--execute`; that performs the migration, reads the resulting scheduling
inventory, and compares a fresh-process doctor report. It never runs a cycle,
refreshes watch policy, records simulated service, requests a source, changes
controls, activates policies, or publishes a dataset.

The H14 runtime source is not yet frozen. Its eventual `h14-source.json` must
have format `h14-reviewed-source-v1`, schema 13, `acquisition_enabled: false`,
`repairs_activated: false`, and an exact `files` SHA256 map. The command and gate
must independently pin that receipt hash. Runtime modules and the reused H11
verification helper must import from that frozen source; future worktree schemas
are refused.

## Verified predecessor

Production H13 is schema 12, source
`/nix/store/mn94ln72schglx9fb1qq8k48bv8nl37a-source`, receipt
`ad95f42e33c551986af2ddaa0199937e4d4ef7bff9f88817f108f412216d0f7e`.
The private backup being prepared is
`/var/lib/swingset/checkpoints/h13-before-h14-20260913-closure-fix`.
Its actual completed remote-verification receipts must be retained in
`/var/lib/swingset/operations/h14-20260913` before preflight can pass.

The backup receipt identifies that deployed H13 source separately from the new
H14 source. It also records `verification_source_receipt_sha256` as
`e359136cb07b2174c8ddd2394bceedb615abeb64e4ca1ec9a157e63c92320f5e` and
`verification_source` as
`/nix/store/0yq6dbr63yldsr1fkgfzyvqpm2vax1sg-swingset-h13-checkpoint-fix-source`.
That verifier fixes nested checkpoint-manifest closure. Every nested
`checkpoint.json` is retained and hashed; only the root manifest excludes itself.

The public baseline must remain V4 commit
`81121dcc7d62a1db9e76ee1c49a6b90e1f4c5653`. Preflight verifies its manifest and
declared files, the private backup's complete size/hash closure, and immutable
SQLite integrity. Checkpoint reads use `mode=ro&immutable=1` and close explicitly.
Extra WAL/SHM files fail closure.

## Coordinator gate and command

Replace every `ACTUAL_*` field with reviewed evidence; placeholders are not an
approval or a verification result. The gate's booleans attest completed remote
checks; this offline driver cannot establish remote truth.

```json
{
  "format": "h14-operational-gate-v1",
  "v4_verified": true,
  "v4_commit": "81121dcc7d62a1db9e76ee1c49a6b90e1f4c5653",
  "v4_manifest_sha256": "ACTUAL_V4_MANIFEST_SHA256",
  "private_backup_verified": true,
  "private_backup_commit": "ACTUAL_PRIVATE_BACKUP_COMMIT",
  "private_backup_receipt": "backup.json",
  "checkpoint": "/var/lib/swingset/checkpoints/h13-before-h14-20260913-closure-fix",
  "checkpoint_manifest_sha256": "ACTUAL_CHECKPOINT_MANIFEST_SHA256",
  "source_receipt_sha256": "ACTUAL_H14_SOURCE_RECEIPT_SHA256",
  "driver_sha256": "ACTUAL_REVIEWED_DRIVER_SHA256",
  "verified_at": "ACTUAL_REMOTE_VERIFICATION_TIME",
  "evidence_files": {
    "backup.json": "ACTUAL_BACKUP_RECEIPT_SHA256",
    "private-verification.json": "ACTUAL_PRIVATE_VERIFICATION_SHA256",
    "v4-verification.json": "ACTUAL_V4_VERIFICATION_SHA256"
  }
}
```

```sh
PYTHONDONTWRITEBYTECODE=1 \
PYTHONPATH=ACTUAL_H14_SOURCE/src:ACTUAL_H14_SOURCE \
  /var/lib/swingset/venv/bin/python \
  /var/lib/swingset/operations/h14-20260913/accept_h14.py \
  --source ACTUAL_H14_SOURCE \
  --source-receipt-sha256 ACTUAL_H14_SOURCE_RECEIPT_SHA256 \
  --state /var/lib/swingset \
  --gate /var/lib/swingset/operations/h14-20260913/gate.json \
  --output /var/lib/swingset/operations/h14-20260913/preflight.json
```

After reviewing preflight, the coordinator adds `--execute` with a new
`execution.json` output. Preserve the VM's library environment. The scheduled
service hold stays present; the driver checks relevant services and timers and
takes `state.lock` for execution. Any unsettled admission requires prior
coordinator reconciliation. Existing pauses remain authoritative and unchanged.

## Acceptance evidence

Every existing user table is compared on its original columns, including all
controls, attempts, budgets, pending work, admission decisions, identity state,
and H11 cohorts. Only schema metadata changes. Backup-upload `last_backup*`
metadata may differ from the checkpoint before execution; all live values must
remain unchanged during execution.

The migration must leave request attribution, offline-service attribution, and
metadata-attempt tables empty. Parent links must equal the existing watch parent
and creation-snapshot references exactly, with unknown first-seen times left
null. Publication fences must cover every required table. No invented service,
request budget, age credit, metadata attempt, or parent discovery is accepted.

Read-only policy inventory reports due counts, unchanged host budgets, remaining
ordinary capacity, backpressure, and one eligible watch/offline selection hint.
It does not execute those hints. JSON and human doctor inventories must agree;
a fresh frozen-runtime process must return the same fixed-time report hash.
All old and new table contents are checked again afterward. Reports stay beside
the operations receipt, outside source and evidence roots.

The default bound is 900 seconds, configurable from 1 to 1800. Failures after
migration retain a failed receipt and the scheduled hold; the driver never
retries an already migrated state automatically. Offline H14 stress tests, not
production service simulations, establish fairness and request-accounting
behavior. Production migration acceptance is still outstanding.
