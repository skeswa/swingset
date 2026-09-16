# Preparing the missing-work inventory deployment (H11)

> Historical record. Dates, status claims, and instructions below describe the
> original work. See [current status](../../../docs/status.md) before acting.
> [Project journal](../../README.md)

The missing-work inventory records gaps and why they need attention. This record concerns its checks or deployment preparation. The original work ID is H11.

These helpers are prepared for the deployment coordinator. They have only run
against synthetic offline states. They do not deploy, request source pages,
accept input bundles, advance admission, build, publish, release the hold, or
start timers. H12 remains separate.

## Assemble after the V4 pin is acknowledged

Use the final V4 source as the base. The currently prepared V4 pin is
`/nix/store/acbgadnny89l7kp459im2lx6bp2ll39q-source`; its publication was still
pending when this procedure was written. The assembler does not infer that a
source pin has been published.

```sh
python -m journal.tools.runtime.assemble_h11_source \
  --base /nix/store/acbgadnny89l7kp459im2lx6bp2ll39q-source \
  --reviewed /Users/skeswa/repos/skeswa/swingset \
  --output /var/tmp/swingset-h11-release-source
```

The output must be new and outside both input trees. The script copies every
base file, overlays its explicit H11 file list, and retains hashes for the
base and final trees in `h11-source.json`. `db.py` must differ only by schema
9→10. Every other changed path must be on the H11 list. The build memory fix,
source adapters, CLI, cycle, identity policy, and admission code come from V4.
Future repair code and the new fixture runner are not copied from the working
tree. Existing base files are preserved even if they are research utilities.

Review `changed` and the hashes before packaging or activation. The coordinator
owns deployment. Run the acceptance helper with `PYTHONPATH` pointing to this
exact assembled source, using the existing VM Python and library environment.
The source tree must retain `config/` and its assembly receipt; the driver
checks the imported database module against the supplied tree.

## Required operational gate

After V4 publication and its verified private backup, retain a JSON gate next
to the actual verification evidence. Example field names follow; placeholders
are not valid evidence and must be replaced with recorded results:

```json
{
  "format": "h11-operational-gate-v1",
  "v4_verified": true,
  "v4_commit": "ACTUAL_REMOTE_VERIFIED_V4_COMMIT",
  "v4_manifest_sha256": "ACTUAL_V4_MANIFEST_SHA256",
  "private_backup_verified": true,
  "private_backup_commit": "ACTUAL_VERIFIED_PRIVATE_COMMIT",
  "checkpoint": "/var/lib/swingset/checkpoints/ACTUAL_V4_CHECKPOINT",
  "checkpoint_manifest_sha256": "ACTUAL_CHECKPOINT_MANIFEST_SHA256",
  "verified_at": "ACTUAL_VERIFICATION_TIME",
  "evidence_files": {
    "v4-remote-verification.json": "ACTUAL_SHA256",
    "private-backup-verification.json": "ACTUAL_SHA256"
  }
}
```

Evidence paths must remain inside the gate directory. The driver verifies
these files, the acknowledged local V4 baseline, the schema 9 backup manifest
and its complete file closure. It opens checkpoint SQLite with
`mode=ro&immutable=1` and explicitly closes the connection. WAL/SHM extras fail
closure verification. It does not contact the remote: the gate records the
coordinator's completed remote verification rather than manufacturing it.

The live state argument is **`/var/lib/swingset`**, not a nested `state/`
directory. Both preflight and execution require its persistent `operator-hold`
file, no active publication intent, and all three services and timers stopped.
Service unit text must contain only the exact negative hold condition, without
condition resets or extra conditions, and `ConditionResult=no`. This reflects
the inspected host; an unfamiliar unit layout is an inconclusive preflight,
not an invitation to start a service. The helper never changes systemd.

## Preflight, then coordinator execution

```sh
PYTHONPATH=/var/tmp/swingset-h11-release-source/src \
  /var/lib/swingset/venv/bin/python \
  /var/tmp/swingset-h11-release-source/research/accept_h11.py \
  --source /var/tmp/swingset-h11-release-source \
  --state /var/lib/swingset \
  --gate /var/tmp/h11-operations/gate.json \
  --output /var/tmp/h11-operations/preflight.json
```

Receipt paths, including the derived doctor files, must be new and outside
checkpoints, candidates, baselines, and all supplied source trees. Symlinked
paths are resolved before this check. Rejection happens before preflight or
creation of the process lock.

Without `--execute` this only reads and writes its new output receipt. The
coordinator repeats the command with `--execute` and a different new output
path after review. Preserve the VM's existing `LD_LIBRARY_PATH` if required.
The execute path holds the pipeline process lock from preflight through the
restart read. It captures before/migrated/after hashes for pause controls,
host budgets, pending work, accepted input digests, watches, snapshots,
identity decisions, and existing cohort membership. It also protects admission
policies/reviews/decisions, source-unit selection, generation fingerprints and
states, the admission selection digest, journal acceptances, source references,
bindings/migrations, link resolutions, the journal token, and accepted-input
meta pointers. Large immutable generation payloads are identified by their
existing fingerprints rather than serialized again. It adds only migration
10, resets the shadow scan cursor, and scans at most 2,000 pages of 100 scopes
by default. A limit exit retains the durable cursor and an incomplete receipt;
it does not claim acceptance. A failed execution is not automatically retried
because the schema may already have migrated; inspect the retained receipt
and current state before any recovery action.

The scan can reconcile findings and append requirement transitions. It cannot
fetch, consume pending derivation work, or execute repairs. After a complete
scan it captures a fresh fixed cohort, verifies zero outside discoveries,
reads doctor, retains JSON and human inventory output, verifies every inventory
field is represented in the human view, and starts a separate interpreter for
a restart read. Legacy cohorts retain NULL cutoffs; no discovery ordering is
invented. The receipt includes the actual queue/lag fields in doctor output,
scan bounds/timing, schema transition, integrity result, and restart checks.

A passing receipt completes this bounded H11 operational check. It does not
publish a dataset or enable H12. Keep the hold until the coordinator proceeds
according to the plan.

## Offline validation

`tests/test_h11_operational_preparation.py` verifies selective assembly,
protection against unrelated `db.py` changes, stopped-unit/hold checks,
checkpoint refusal, immutable backup verification and evidence tampering,
complete human/JSON inventory representation, value-sensitive input hashes,
and an actual synthetic schema 9→10 scan with a separate-process doctor read.
Additional regressions independently change admission/journal semantic state
while keeping the old accepted-input and decision tables unchanged, and reject
unsafe receipt locations before any preflight writes.
