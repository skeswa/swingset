# Extension restore rehearsal preparation, 2026-09-17

The [helper](../../tools/runtime/rehearse_extension_restore.py) is implemented
and tested offline. No production restore has been executed by this preparation
branch. The coordinator owns helper freezing, independent review and execution.

## What it exercises

The helper calls the actual `restore_from_checkpoint` protocol on a new
disposable directory. That protocol installs `RESTORE_PENDING`, verifies and
copies the checkpoint closure, recreates the baseline, checks the public head
twice and activates the restored state. A read-only wrapper verifies the exact
remote baseline candidate, manifest and all candidate file hashes, while
requiring the barrier and byte-exact operator hold throughout remote checks.

The resulting schema-14 state remains held. Only then does `open_database`
migrate it to the explicitly pinned target schema. Stable table receipts compare every pre-existing
application table, excluding only the schema-version metadata row. Integrity
and foreign keys must pass; reopening must change nothing. Every copied private
file other than the migrated database must match the checkpoint. The checkpoint
itself and the frozen runtime are rechecked afterward.

The helper starts no workers, runs no cycle, accepts no inputs and activates no
automatic repairs. Both live ordinary-unit checks require all six cycle,
backup and summary services/timers to be inactive and the live hold to remain.
The restored hold is never removed. Public reads use only GET/HEAD, a serial
client, project User-Agent, five-second host spacing and a 256-request ceiling.
The serial transport keeps its mutex through streamed body closure and anchors
spacing at completion or transport failure, not a pre-dispatch request hook.
Xet's separate download pool is disabled. Remote cache files stay inside the
disposable operation directory, not the live worker's home.

Failed remote checks retain `RESTORE_PENDING`. Later migration/preservation
failures retain the operator hold and a failed operation receipt. The helper
never attempts public repair or retries an unknown public commit. Existing
destinations are refused rather than deleted by this wrapper.

## Pins and invocation

The reviewed runtime freeze is
`/nix/store/01qy6sk23bjdpyh64ky4v8hg055rz1cx-source`, with
`extension-source.json` SHA-256
`a0fe7dd42bf05ebe49a35197111799ba6325f16376f3263ff6b2c352b7919283`.
The helper imports the pinned migration helper from that inventory to reuse
source verification and stable table receipts. Its own separate file is pinned
by `--helper-sha256`; it need not rewrite the runtime freeze.

The first acknowledged checkpoint remains a valid rehearsal input:

- Path: `/var/lib/swingset/checkpoints/h16-published-20260917`.
- Manifest: `0fd7406ae2fc1f0014ced9a083894693af76079359231bc6613b00becc69275b`.
- Private acknowledgment: `dd33cd55e23765fe9479fc3a9f8b265299a2e868`.

A later pre-deployment backup is separate and must preserve the eight subsequent
paid Archive request debits. Supply its actual manifest and acknowledgment pins
if using that checkpoint. The helper records the archive pin but does not
pretend to upload or independently establish its acknowledgment.

Under the coordinator's provisioned service credentials and frozen Python
dependencies, use the separately frozen helper with these arguments:

```sh
PYTHONDONTWRITEBYTECODE=1 \
PYTHONPATH=/nix/store/01qy6sk23bjdpyh64ky4v8hg055rz1cx-source/src \
python /path/to/frozen/rehearse_extension_restore.py \
  --helper-sha256 <reviewed-helper-sha256> \
  --source /nix/store/01qy6sk23bjdpyh64ky4v8hg055rz1cx-source \
  --source-receipt /nix/store/01qy6sk23bjdpyh64ky4v8hg055rz1cx-source/extension-source.json \
  --source-receipt-sha256 a0fe7dd42bf05ebe49a35197111799ba6325f16376f3263ff6b2c352b7919283 \
  --checkpoint /var/lib/swingset/checkpoints/h16-published-20260917 \
  --checkpoint-sha256 0fd7406ae2fc1f0014ced9a083894693af76079359231bc6613b00becc69275b \
  --archive-commit dd33cd55e23765fe9479fc3a9f8b265299a2e868 \
  --target-schema 27 \
  --public-repo skeswa/swingset \
  --destination /var/tmp/swingset-extension-restore-20260917-001
```

The destination must not exist or overlap production, checkpoint or frozen
source paths. Allow disk space for the full restored checkpoint and a separate
public-download cache. Use disk-backed temporary storage and the coordinator's
bounded transient service. Do not pass a migration-only specimen as a checkpoint.

## Offline validation and remaining gates

Four tests use actual checkpoint installation and restore functions with only
the public transport mocked. They verify successful held activation, rejection
of reused destinations and remote writes, changed first/second public heads,
manifest mismatch and retained failure barriers. Two additional transport tests
inject variable pre-dispatch latency, a delayed streaming body and a send
exception; actual dispatches retain the five-second floor after completion.
All six passed against the frozen schema-27 source. Ruff and mypy pass the helper.
These fixtures do not represent the production checkpoint's volume or service
timing, and no remote calls were made during their execution.

The [source-bound offline receipt](../../evidence/runtime/extension-restore-helper-2026-09-17/offline-check-001/checks.json)
records six tests passing in 0.62 seconds plus Ruff and mypy, with unchanged
helper/test bytes and the exact runtime receipt. The tested helper SHA-256 is
`55f40f2533da2de3ebd37368cbccf9fd60659dccd5c7067c3444c094e43eb35c`.
Coordinator formatting or changes require a new helper pin and validation;
this retained receipt must not be rewritten.

Before operational use, freeze and independently review the final helper bytes.
The target-schema flag must match that frozen runtime. A newer schema-28
deployment uses its own reviewed runtime inventory, target flag and latest
verified checkpoint pins; the earlier schema-27 invocation above is not silently
retargeted to those new bytes.
Record the concrete execution receipt, copied-state preservation and measured
elapsed time. Keep input reconciliation, deployment, ordinary job activation,
extension cohort observation, publication and H18 activation as separate gates.

See [D-0058](../../decisions/0058-rehearse-the-real-restore-protocol-under-hold.md).
