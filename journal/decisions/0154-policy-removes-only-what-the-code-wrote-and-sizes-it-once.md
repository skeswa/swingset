# D-0154: Checkpoint policy removes only what the code wrote, and sizes it from the manifest

Status: Proposed  
Recorded: 2026-09-18  
Accepted: —  
Acceptance source: —  
Topic: Worker disk space  
Supersedes: —  
Superseded by: —

## Decision

Three corrections to the checkpoint pruning that
[D-0152](0152-the-backup-command-prunes-its-own-checkpoints.md) wired into
`swingset backup` and [D-0153](0153-checkpoint-cleanup-never-fails-the-thing-it-cleans-up-after.md)
amended. They change `backup/pruning.py`, `cli.py`, and the three documents
D-0153 rule 5 rewrote.

1. **Policy removes only a name the code wrote.** A `run_*` checkpoint, or a
   `.<name>.tmp-<hex>` directory from an interrupted `create_checkpoint`. Every
   other name is an operator's, and policy leaves it alone at any age, whether
   or not it holds a `checkpoint.json`. `--remove-checkpoint NAME` removes one,
   complete or not. A temporary copy goes by the incomplete limit even when it
   does hold a manifest, because `create_checkpoint` writes that inside the
   copy and renames afterwards, so a crash in between leaves a complete
   directory no rename will claim. This replaces D-0153 rule 5, which
   documented the opposite rather than changing the code.
2. **Every size comes from the manifest, in the plan as well as the report.**
   `_size` reads `checkpoint.json` and walks only a directory with no usable
   one. D-0153 rule 3 put that in `describe` alone, but `describe` calls
   `plan_prune` first, and the plan sized what it would remove by walking, so
   the shortcut applied to exactly the directories it was not needed for.
3. **The backup works the plan out once.** `remove_planned(checkpoints, plan)`
   applies a plan the caller already has; `prune` is that plus `plan_prune`.
   `_prune_checkpoints` plans once, reads the plan to refuse a plan naming this
   run's own checkpoint, and applies the same plan. It checks no restore
   marker, because `open_database` refuses a state directory holding
   `RESTORE_PENDING`, so a backup cannot start while a restore into that
   directory is pending.

## Why

Wiring pruning into the backup turned a module nothing called into a timer that
runs unattended three times a day. Two behaviors that were harmless while
nothing called them are not harmless now.

Removing an operator-named directory by age is the rule
[D-0133](0133-bound-the-worker-disk-and-remove-rehearsals-eagerly.md) rejected,
in an accepted record: "a held checkpoint guards a rollout gate whose duration
is not known in advance." A hold staged with `cp -a` and interrupted -- and
`ENOSPC` interrupting a copy is the exact condition this work exists for --
holds no `checkpoint.json`, so the old rule had the backup timer remove it
under an operator who was still staging it. The plan's section 4 says no step
removes the only checked copy of any data, and a partial copy of a candidate
the live state has since dropped is such a copy. Leaving it costs disk that
doctor now names, which is the smaller harm.

The sizing was measured, not guessed. With three complete checkpoints, one past
the age limit, `describe` walked the prunable one and read the manifest for the
kept two; on the worker's 2026-09-18 state, fifteen of seventeen checkpoints
were prunable, so the shortcut covered two. `doctor --watch` defaults to five
seconds and `summary` runs daily, both through `describe`. The double plan was
counted the same way: exactly two walks per removable checkpoint per backup,
inside `_mutate` while the backup holds the writer lock, so a `cycle --timer`
in that window exits `skipped-overlap`.

## Alternatives

- Keep the age rule for unfinished directories and document it, as D-0153 did.
  Rejected: the document changed to match the code, when the code was the part
  contradicting an accepted decision. D-0153 priced the alternative itself --
  "a small change to `plan_prune` and one test".
- Have the operator stage a hold under a temporary name and rename it. Rejected
  as the only protection: it is a rule a person has to remember on the day the
  disk is full, and forgetting it removes the hold.
- Size from the manifest only in `describe`, and keep the walk in the plan.
  Rejected: `describe` plans first, so the walk happens either way.
- Have `prune` cache its last plan. Rejected: a cache that can go stale, in
  place of a caller passing the plan it already has.
- Give the automatic prune a `RESTORE_PENDING` check to match
  `--remove-checkpoint`. Rejected: dead code. A backup on a state directory
  with that marker exits before it writes anything.

## Consequences

An unfinished operator-named copy now stays until somebody removes it by name,
so a forgotten one holds a full state copy's worth of disk. Doctor lists it as
incomplete with its bytes, which is the only warning.

A plan's `reclaimable_bytes` is now the manifest's total, not the bytes on
disk, for a checkpoint that has a manifest. A checkpoint edited by hand after
it was written is reported at the size it was written.

A restore into a different state directory that reads a checkpoint from this
one is still not protected, in either command: nothing on this machine marks
it. Renaming the checkpoint before such a restore does protect it, because
policy no longer removes any operator-named directory.

## Links

- [D-0152](0152-the-backup-command-prunes-its-own-checkpoints.md),
  [D-0153](0153-checkpoint-cleanup-never-fails-the-thing-it-cleans-up-after.md),
  [D-0133](0133-bound-the-worker-disk-and-remove-rehearsals-eagerly.md)
- [Operation guide](../../docs/guides/operation.md#disk-space),
  [operations reference](../../docs/reference/operations.md#backup-and-restore),
  [state contract](../../docs/reference/state.md#knobs)
- [Worker disk investigation](../investigations/2026/worker-disk-exhaustion-2026-09-18.md)
- [Bounded state plan](../../docs/plans/bounded-state-and-archive.md)
