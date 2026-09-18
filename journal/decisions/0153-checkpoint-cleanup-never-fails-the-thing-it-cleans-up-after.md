# D-0153: Checkpoint cleanup never fails the backup or the health check

Status: Proposed  
Recorded: 2026-09-18  
Accepted: —  
Acceptance source: —  
Topic: Worker disk space  
Supersedes: —  
Superseded by: —

## Decision

Nothing locks `state/checkpoints`. A backup writes a temporary directory there,
renames it into place and removes old ones, while doctor, summary and a second
operator read the same directory. Four rules follow, and they amend
[D-0152](0152-the-backup-command-prunes-its-own-checkpoints.md), which wired
pruning into the backup command:

1. **Pruning cannot fail a backup that worked.** `_prune_checkpoints` catches
   everything and logs `event="checkpoint-prune-failed"` with the names that
   did go. Before this, a directory that `rmtree` could not empty made the
   whole command exit 1 after the checkpoint was written, uploaded and
   recorded in `meta`, so the database said the backup happened and the exit
   status said it failed. `nix/module.nix` restarts `swingset-backup` on
   failure every 60 seconds with no start limit, so one unremovable directory
   would mean a fresh multi-gigabyte copy and an archive commit every minute.
2. **Every walk skips an entry that disappears under it.** `_tree_bytes`,
   `plan_prune`, `describe` and `prune` treat a vanished path as nothing to
   decide about, and `prune` counts an already-removed directory as removed.
   Doctor otherwise exits 1 with a bare `FileNotFoundError` whenever it runs
   while a backup renames or prunes, which is every overlap of the 04:00,
   12:00 and 20:00 backup timers with `summary`, `doctor --json` or
   `doctor --watch`. A removal that genuinely cannot finish still raises
   `PruneError`, carrying what did go.
3. **Doctor sizes a complete checkpoint from its manifest.** `checkpoint.json`
   records a size for every file, and `verify_checkpoint` checks those against
   the bytes on disk, so one read replaces one `stat` per file. Only a
   directory with no usable manifest is walked. D-0152 rejected this on the
   grounds that parsing the manifest is no cheaper than the walk; that compares
   one open and one JSON parse against hundreds of thousands of syscalls. The
   worker held 17 checkpoints of a full state copy on 2026-09-18, and
   `doctor --watch` repeats the measurement every five seconds.
4. **`--remove-checkpoint` refuses while `RESTORE_PENDING` exists, and an
   empty name is an error.** D-0152 said the removal needs no lock because the
   pipeline does not read the directory. That is not true: `Archive` recovers
   missing artifacts from local checkpoints through `LocalCheckpointRecovery`,
   and `restore --checkpoint` reads one file by file for minutes. Recovery
   tolerates a checkpoint disappearing (it records the failure and tries the
   next one); a restore does not, so a restore in progress refuses the
   removal. The dispatch tests `--remove-checkpoint is not None`, so
   `swingset backup --remove-checkpoint "$NAME"` with `NAME` unset fails
   instead of taking a backup.

A fifth point is a documentation correction, not a code change. It is
reversed by [D-0154](0154-policy-removes-only-what-the-code-wrote-and-sizes-it-once.md),
which changes the code instead, and that record also widens rule 3 to cover the
plan as well as the report; the paragraph below describes the behavior as it
was when this record was written.
`plan_prune` removes any directory with no `checkpoint.json` once it is past
`checkpoint_incomplete_max_age`, whatever it is named, and
`test_pruning.py::test_incomplete_directories_are_removed_only_after_their_limit`
has pinned that since D-0133. Three documents said policy never removes an
operator-named checkpoint. They now say it never removes one _by age_, and that
a directory with no manifest is not a checkpoint: stage a hold under a
temporary name and rename it once the copy is complete.

## Why

The wiring was reviewed against the worker's real timers. Every finding above
was reproduced before it was fixed: a `chmod 0500` subdirectory made a
successful `backup --local` exit 1; a `stat` that fails after `is_file()`
succeeded made `doctor --json` exit 1 with `event="error"`; an empty
`--remove-checkpoint` name took a full backup; an operator-named incomplete
directory three days old was removed with reason "incomplete checkpoint older
than the limit".

Rule 1 is the shape of the plan's section 4 safety rules applied to cleanup: a
step that removes extra copies is not a step whose failure should invalidate
the copy that was just made. Rule 2 is the same idea for a report: a health
check that fails because a backup is running tells an operator nothing.

Correcting the documents rather than the module keeps the frozen pruning
behavior and its tests intact, which the slice required. The exception is also
the right behavior for the case it was written for -- `create_checkpoint`
leaves `.<name>.tmp-<uuid>` directories behind when it is interrupted -- and a
half-copied directory under any name is not a checkpoint anyone can restore.

## Alternatives

- Restrict the incomplete rule to `run_*` and `.…tmp-…` names, so an
  operator-named half-copy survives. Rejected here: it contradicts a frozen
  test the slice was told not to redesign, and it leaves an unrestorable
  directory on a full disk forever. If the owner prefers it, it is a small
  change to `plan_prune` and one test.
- Let a prune failure fail the backup, so nobody can ignore it. Rejected: the
  unit's restart loop turns that into more writes on a disk that is already
  full, and the log line plus doctor's list already surface it.
- Have doctor take the control lock before listing checkpoints. Rejected: a
  health check that blocks on a running backup is worse than a report that
  skips a directory in flight, and the lock would not cover a plain `mv`.
- Give `--remove-checkpoint` the writer lock. Rejected for the reason D-0152
  gives; the `RESTORE_PENDING` refusal covers the one reader that cannot
  tolerate the removal.

## Consequences

A backup now reports success while leaving disk unreclaimed, so
`event="checkpoint-prune-failed"` and doctor's checkpoint list are the only
signs; an operator who reads neither sees the disk fill. Sizes in doctor's
report come from the manifest, so a checkpoint whose files were edited by hand
after it was written would be reported at its manifest size, not its size on
disk. A removal racing a restore that targets a _different_ state directory is
still possible; only a restore into the same state directory is refused.

## Links

- [D-0154](0154-policy-removes-only-what-the-code-wrote-and-sizes-it-once.md),
  which replaces rule 5 and widens rule 3
- [D-0152](0152-the-backup-command-prunes-its-own-checkpoints.md),
  [D-0133](0133-bound-the-worker-disk-and-remove-rehearsals-eagerly.md)
- [Operation guide](../../docs/guides/operation.md#disk-space),
  [operations reference](../../docs/reference/operations.md#backup-and-restore),
  [state contract](../../docs/reference/state.md#knobs)
- [Worker disk investigation](../investigations/2026/worker-disk-exhaustion-2026-09-18.md)
- [Bounded state plan](../../docs/plans/bounded-state-and-archive.md)
