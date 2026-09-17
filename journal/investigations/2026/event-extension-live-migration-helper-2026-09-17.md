# Guarded live migration helper — 2026-09-17

`journal/tools/runtime/accept_event_extension.py` prepares a coordinator-owned
schema-14→28 migration. Its default invocation only reads production state and
emits a preflight receipt. It uses the frozen migration rehearsal's source and
table verifiers as read-only imports. The operational helper itself is frozen
separately and its exact bytes must match the reviewed gate.

The reviewed runtime is candidate 003:

- Source: `/nix/store/dx0cyzvd8d91rf29v21sakbr7l5bwxnz-source`.
- Source receipt SHA-256: `60cdfe64004a0ba5a9aee207bcb089d6d4c81f5ca58169ccdb88a5dfab8146d6`.
- New system: `/nix/store/lzwkabfmbz46d05yi5k41nq56i7jjh74-nixos-system-swingset-lxc-25.11.20260630.b6018f8`.

Candidate 002's full run failed two tests. Its failed receipt cannot authorize
this helper. The two test corrections exercise the intended spacing interlocks:
a restarted partial turn waits the full retained gap without another debit, and
schema-17 reporting reads a retained accounting fixture without invoking current
HTTP acquisition on an unsupported schema. No runtime change was needed.

## Required gate

The gate is a reviewed JSON object with format
`event-extension-live-migration-gate-v1`. It contains:

- `state`: exactly `/var/lib/swingset`; `source`: the pinned source above.
- `helper_sha256`: the separately frozen operational helper's hash.
- `phase`: `before_deployment` for read-only inspection of the old active and
  persistent system, or `after_deployment` for the new system. `--execute`
  requires the latter.
- `source_receipt`: `{path, sha256}` for the complete frozen source inventory.
- `checkpoint`: `{path, manifest_sha256}` for the fresh acknowledged schema-14
  checkpoint retaining candidate `cand_8f31cad7226643ae` and public commit
  `2a6c7dc744fb36eabb5163c0a527d787d3721f4f`.
- `restore_helper`: `{path, sha256}` for the exact helper that produced the
  restore receipt. Its hash must match that receipt. The migration rehearsal
  helper is checked against the frozen source inventory itself.
- `evidence`: exactly eight `{path, sha256}` references named `backup`,
  `migration`, `restore`, `validation`, `service_binding`, `pytest_log`,
  `ruff_log`, and `mypy_log`. Paths can be absolute or relative to the gate;
  parent traversal is rejected. Gzip JSON receipts are supported.

All upstream receipts must be closed with `finished_at` and `passed: true`.
Validation additionally needs the actual source before/after checks, schema 28,
zero check exit codes, `pytest.full_suite: true`, a positive passed count, zero
failures and the exact separately pinned log hashes. Rehearsals must name the
same checkpoint and source, preserve all 71 predecessor tables and reach schema 28. Restore must finish activation under hold, verify the acknowledged public
baseline, and report no workers, repairs, input acceptance or public writes.

Service binding must identify the reviewed new system and package, include
`unit_hashes` for all three ordinary services (timer hashes are also allowed),
and report `external_overrides_match`, `hold_conditions`, and `backup_disk_tmp`
as true. `external_files` maps absolute external configuration/override file
paths to their reviewed hashes. The helper rechecks those actual files and unit
files; a stale successful receipt is insufficient.

## Invocation and preservation

Use the reviewed runtime's Python environment and source path. Supply the exact
reviewed gate hash:

```sh
python /path/to/frozen/accept_event_extension.py --gate /path/to/gate.json --gate-sha256 REVIEWED_SHA256
```

Only the coordinator adds `--execute`, after deploying the reviewed system under
the existing hold and closing every prerequisite. The helper takes the existing
writer and control locks, preserving their lock order. It rejects unsettled
execution admissions and pending publication. It compares all predecessor table
rows/columns and all retained checkpoint files to live state. Only the schema
metadata value is excluded from row hashes and is checked separately.

Long preflight scans do not authorize a later write from stale checks. Source,
helper, gate, receipt, external-file and operating guards are rechecked immediately
before migration, after the repeated locked table scan. The result must match the
complete rehearsed table receipts, survive reopening, and preserve retained files
and guards. A partial migration failure emits an explicit failed receipt and
requires coordinator inspection; no automatic rollback is claimed.

No fetching, derivation, runtime-input acceptance, publication, spacing-baseline
adoption, service activation or system deployment occurs here. New schema-28
legacy spacing interlocks remain unknown for separate reviewed reconciliation.

## Local validation and limits

The initial 25 offline guard tests passed in 0.18 seconds. After the last-boundary
guard was added, **26 tests passed in 0.31 seconds**. The latter includes a real
disposable schema-14→28 migration preserving all 71 tables, a changed prestate
that remains schema 14, and an external-file change during a long read that is
rejected before migration. Ruff and mypy for the helper passed. These are
overlapping local receipts, not an integrated production validation run.

Independent review found no remaining blocker after that guard fix. Its run
against the frozen candidate-003 runtime passed **26 tests in 0.24 seconds**.
Reviewed helper SHA-256:
`459f9157b04f6449792d1eb6fac1c79885d50107fb4cd4aad84c7192218d3029`.
Reviewed test-file SHA-256:
`521dcd44320875cadcbd4a6fa586dc56679ca78a146b7a815c2be582c0bcf882`.
These hashes precede coordinator formatting; formatting needs fresh helper pins.
A separately retained
[independent check](../../evidence/runtime/extension-live-migration-helper-2026-09-17/independent-check-001/checks.json)
records the exact command, environment, source receipt and unchanged helper/test
hashes. Its repeat run passed 26 tests in 0.21 seconds. The receipt records the
resolved last-boundary finding and no remaining reviewer blocker; it does not
claim operational acceptance.

The coordinator's candidate-003 full suite, fresh backup, schema-28 migration and
restore rehearsals, service bindings, and eventual live execution remain distinct
receipts. No production action was performed by the helper implementation task.

See [D-0063](../../decisions/0063-guard-live-extension-migration-with-closed-receipts.md).
