# D-0152: The backup command prunes its own checkpoints, by the retention table

Status: Proposed  
Recorded: 2026-09-18  
Accepted: —  
Acceptance source: —  
Topic: Worker disk space  
Supersedes: —  
Superseded by: —

## Decision

`swingset backup` calls `backup/pruning.py` once its checkpoint is written, and
uploaded when it had to be. Four choices come with that:

1. **The knobs live in the `[retention]` table.** `checkpoint_keep_recent`
   (default 2), `checkpoint_max_age` (default two days) and
   `checkpoint_incomplete_max_age` (default one day) join the three retention
   values in `config/sources.toml`, parsed into `RetentionConfig`. The defaults
   are the `PrunePolicy` defaults, so nothing changes for an operator who never
   adds the table. `--no-prune` skips pruning for one run.
2. **The new checkpoint is checked out of the plan first.** The plan is worked
   out once, and if it names the directory this run just wrote, nothing is
   pruned and the skip is logged. Only a `--destination` that reuses a timer
   name older than the ones already there can reach this, and only under a
   policy that keeps one and allows no age. It is a guard, not a normal path.
3. **`--remove-checkpoint NAME` takes no lock and starts no run.** It removes
   one operator-named checkpoint and makes no backup, so it captures no input
   bundle, writes no run record and does not wait for the writer. Combining it
   with `--local`, `--destination` or `--no-prune` is an error rather than a
   silent choice between removing and backing up.
4. **Doctor measures every checkpoint.** `pruning.describe` reports name,
   completeness, age in days, bytes and whether policy would prune each one,
   and removes nothing. The plan measures only what it would remove, because
   it is about to walk those directories anyway; a report is for a person
   deciding what to remove by name, so it measures all of them, from each
   manifest ([D-0153](0153-checkpoint-cleanup-never-fails-the-thing-it-cleans-up-after.md)
   rule 3).

## Why

[D-0133](0133-bound-the-worker-disk-and-remove-rehearsals-eagerly.md) left the
pruning module implemented and tested but called by nothing, and
[the operation guide](../../docs/guides/operation.md#disk-space) already
described the behavior this change makes true. Seventeen checkpoints held 97 GB
on 2026-09-18 because nothing removed one.

Pruning after the upload, not before, keeps the rule that no step removes the
only checked copy of anything: a failed upload raises before the prune line, so
the old checkpoints are still there to fall back on.

The knobs go in the `[retention]` table because one exists now
([D-0141](0141-two-retention-limits-and-the-collector-age-become-policy.md)) and
it is already how an operator changes what the worker keeps. A second table for
three values of the same kind would mean two homes for one question.

Pruning cannot contradict the retention planner. `checkpoints` is in
`EXCLUDED_TOP_LEVEL`, so the checkpoint file closure skips it, and the planner
walks only `blobs`, `extracts`, `inputs` and `candidates`. A checkpoint
directory is therefore never a root, never a file a plan must keep, and never
reported as unknown.

## Alternatives

- A separate `[checkpoints]` table. Rejected: same kind of policy, second home.
- Leave the three values as `PrunePolicy` defaults in code. Rejected: an
  operator clearing disk pressure would need a release, which is the reason
  D-0141 moved the collector's age floor out of the code.
- Prune before the upload. Rejected: a failed upload would then have fewer
  copies, not more.
- Give `--remove-checkpoint` the writer lock and a run record for symmetry with
  a real backup. Rejected: a removal that waits on a running cycle is a removal
  nobody does. The pipeline does read these directories, as a recovery source,
  but it tolerates one disappearing; a restore does not, so a pending restore
  refuses the removal ([D-0153](0153-checkpoint-cleanup-never-fails-the-thing-it-cleans-up-after.md)
  rule 4).
- Have doctor walk every checkpoint to size it. Rejected in
  [D-0153](0153-checkpoint-cleanup-never-fails-the-thing-it-cleans-up-after.md):
  the manifest already records a size for every file, and one read beats one
  stat per file across seventeen full copies of the state. Only a directory
  with no usable manifest is walked.

## Consequences

Timer checkpoints older than two days are gone after the next backup, which is
the point. Backups prune on the
worker only when the backup runs, so a machine whose backup timer is stopped
still fills up; the scratch timer from D-0133 is unaffected.

`--remove-checkpoint` cannot remove a timer checkpoint. An operator who wants
one gone waits for policy, or renames it and removes it by its new name.

## Links

- [D-0153](0153-checkpoint-cleanup-never-fails-the-thing-it-cleans-up-after.md),
  which amends items 3 and 4 above, and
  [D-0154](0154-policy-removes-only-what-the-code-wrote-and-sizes-it-once.md),
  which amends items 2 and 4
- [D-0133](0133-bound-the-worker-disk-and-remove-rehearsals-eagerly.md)
- [D-0141](0141-two-retention-limits-and-the-collector-age-become-policy.md)
- [Operation guide](../../docs/guides/operation.md#disk-space)
- [Operations reference](../../docs/reference/operations.md#backup-and-restore)
- [State contract](../../docs/reference/state.md#knobs)
- [Worker disk investigation](../investigations/2026/worker-disk-exhaustion-2026-09-18.md)
