# H16 operational acceptance preparation

`research/accept_h16.py` verifies the deployed H16 code against its frozen source
receipt and the separately hashed operations gate. It requires schema 14 and
never opens the database for writing. `--execute` runs additional read-only
checks; it does not migrate, run a cycle, select or materialize a release,
fetch sources, publish, activate repairs, or accept historical years.

The predecessor is the verified private H15 checkpoint:

- Checkpoint: `/var/lib/swingset/checkpoints/h15-before-h16-20260913`.
- Private commit: `c656e88c775b23ae5879661924d57fa1f93cfa7d`.
- Manifest SHA-256: `700b2adc01db9eefc5c0c7ba35fddabfbc3aa00b6ed6f4900543b4e4a54ae444`.
- H15 source receipt: `be7001a7e3bd3bc496f40513662cb5b561217b55cf37a3e11255fa84deea1c8c`.
- Published V4 commit: `81121dcc7d62a1db9e76ee1c49a6b90e1f4c5653`.

The coordinator supplies an `h16-operational-gate-v1` JSON document. It binds
`driver_sha256`, `source_receipt_sha256`, `checkpoint`,
`checkpoint_manifest_sha256`, `private_backup_commit`, `private_backup_receipt`,
`private_backup_verified`, `v4_commit`, `v4_manifest_sha256`, `v4_verified`,
`verified_at`, and `evidence_files` (relative filename to SHA-256). The private
receipt and completed verification evidence must be retained in that last map.
The frozen source contains `h16-source.json` with format
`h16-reviewed-source-v1`, schema 14, `acquisition_enabled: false`,
`repairs_activated: false`, and its exact file hash map. It includes the H11
helper imported by this driver. The H16 source receipt is supplied after review;
no current worktree path qualifies as a deployed pin.

Run the separately hashed driver using the frozen source's Python environment
and module path. Substitute the reviewed source, receipt, and operations paths:

```sh
PYTHONPATH="$H16_SOURCE/src:$H16_SOURCE" "$H16_PYTHON" "$H16_OPS/accept_h16.py" \
  --state /var/lib/swingset --source "$H16_SOURCE" \
  --gate "$H16_OPS/gate.json" --output "$H16_OPS/preflight.json" \
  --source-receipt-sha256 "$H16_SOURCE_RECEIPT" --max-seconds 900
```

After reviewing preflight, the coordinator repeats with `--execute` and an
`execution.json` output. Both paths acquire the existing writer lock and require
the service hold, inactive timers, no unresolved execution admission, and no
publication intent. They verify checkpoint artifact sizes and hashes, including
nested manifests. Checkpoint SQLite is opened immutable; production SQLite is
opened query-only. Output is rejected under source or retained artifact roots.

The `.sizes.json` file records database, source, and estimated checkpoint scale
before expensive scans. Full doctor JSON and human inventory stay in operations
files; the acceptance receipt records their sizes and hashes. Doctor is run in
two processes at one exact clock value and compared byte-for-byte after canonical
JSON encoding. The inventory reports all unfinished scopes, including scopes
without queue hints; its completed-work count remains zero. Every user table,
column, and schema object must match before and after. No new correction clock
values may appear. Only backup bookkeeping metadata may differ from the private
checkpoint before the test.

A retained scratch build and publication acceptance are separate steps. This
receipt proves read-only deployment compatibility, not successful convergence
or a completed H16 release.
