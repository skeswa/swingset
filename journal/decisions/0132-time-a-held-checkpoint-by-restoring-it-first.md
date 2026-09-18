# D-0132: Time a held checkpoint by restoring it first, then backing up what came out

Status: Proposed  
Recorded: 2026-09-18  
Accepted: —  
Acceptance source: —  
Topic: State retention and recovery cost  
Supersedes: —  
Superseded by: —

## Decision

`--time-backup` in
[`journal/tools/runtime/measure_state_storage.py`](../tools/runtime/measure_state_storage.py)
picks its order from the shape of what it was given.

- **A sealed checkpoint** (a directory with a `checkpoint.json` at its top
  level, which is what plan step 1 tells an operator to copy) is restored into
  scratch first, and the restored state directory is then backed up and
  verified. The report calls this order `restore-then-backup`.
- **A plain state directory** is backed up and verified first, and that
  checkpoint is then restored into scratch. The report calls this order
  `backup-then-restore`.

Either way the run writes two whole copies of the state tree under `--scratch`,
and the tool refuses to start unless free disk covers both. The report names
the order, so a reader knows what each duration covers.

## Why

A sealed checkpoint is not a state directory, so it cannot be backed up as one.
`backup/checkpoint.py` copies every non-excluded file out of the directory it
is given, which for a copied checkpoint includes that checkpoint's own
`checkpoint.json`. It records that file in the new manifest and then overwrites
it with the new manifest, while `verify_checkpoint` leaves `checkpoint.json`
out of the set it compares. Verification therefore fails with
`checkpoint closure differs: missing=['checkpoint.json']`. Reproduced against a
sealed checkpoint built locally by
`tests/test_measure_state_storage.py::test_time_backup_restores_a_copied_held_checkpoint_before_backing_it_up`,
not against the worker's held checkpoint: no copy of one exists on this machine.
Unverified: that a worker-sized held checkpoint behaves the same way.

A second, quieter fault came with it. A sealed checkpoint has no `baseline`
symlink; the symlink is created during restore, from the manifest. Backing up a
copied checkpoint directly would therefore find no referenced candidates and
leave every candidate file out of the timed backup, so the timing would cover
fewer bytes than a real backup does.

Restoring first fixes both, and measures the thing the plan actually asks for.
The restore leg includes verifying the source checkpoint, which is work a real
recovery does. The backup leg then runs against a proper state directory, with
its baseline symlink in place, so it copies the same closure a real backup
copies.

The free-disk guard follows from the order: the restore writes one copy of the
tree and the backup writes another. Sizing it on the database file alone was
wrong by roughly 1.75 times on the plan's own section 1 figures, where a
5,016,920,064-byte database has an 8,770,615,161-byte verified checkpoint.

Unverified until the measurement runs: how long each leg takes on the worker's
disk, and whether the restored tree and the new checkpoint differ in size
enough to matter.

## Alternatives

- Back up the copied checkpoint directly and accept the extra
  `checkpoint.json`. Rejected: it does not work at all, and the earlier claim
  in D-0127 that the difference was small and recordable was wrong.
- Delete `checkpoint.json` from the copy before timing. Rejected: it edits the
  copy, so the measured tree is no longer the held checkpoint, and the copy can
  no longer be verified against its own manifest.
- Time only the restore, and leave backup timing out. Rejected: plan section 5
  asks for both, and step 3's reclaim gates are written against backup cost.
- Refuse a checkpoint directory and make the operator restore it by hand first.
  Rejected: the extra step is exactly the one the tool can do, and it is where
  an operator would otherwise skip the verification.

## Consequences

One flag measures both legs on either shape of input, in the order that matches
the input. The cost is disk: two whole copies of the state tree, about 17.5 GB
for the worker's current checkpoint, and the tool refuses below that rather
than failing partway through.

A timing failure no longer discards the measurement. The report and the receipt
are written with the failure recorded as a finding, and the tool exits non-zero.

## Links

- [Implementation plan](../../docs/plans/bounded-state-and-archive.md), section 5
- [Tool design](0127-measure-state-storage-read-only-on-a-copy.md)
- [Investigation](../investigations/2026/state-storage-measurement-2026-09-18.md)
- [Sealed checkpoint read mode](0102-read-sealed-checkpoints-without-sqlite-sidecars.md)
