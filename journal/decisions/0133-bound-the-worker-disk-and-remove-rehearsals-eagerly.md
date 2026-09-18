# D-0133: Bound the worker disk and remove rehearsals and old checkpoints eagerly

Status: Accepted  
Recorded: 2026-09-18  
Accepted: 2026-09-18, Sandile Keswa  
Acceptance source: Session instructions on 2026-09-18: "these rehearsals cannot possibly need to stick around for this long", "update our docs and logic to eagerly remove unneeded rehearsals and checkpoints", "set an upperbound on the volume used by our vm", and approval of the deletions, the machine restart, and the rebuild  
Topic: Worker disk space  
Supersedes: —  
Superseded by: —

## Decision

Three rules for the production worker, which is the OrbStack machine `swingset`:

1. The machine is bounded to 256 GiB (`machine.swingset.disk_bytes`), and the
   restore machine to 64 GiB. OrbStack enforces the bound as a btrfs quota on
   the machine's subvolume. A full machine stops the worker; it no longer stops
   the Mac.
2. Rehearsal scratch lives under `/var/tmp/swingset-<name>` and is disposable
   once its receipt is retained. The `swingset-scratch-clean` timer removes any
   such directory in which nothing changed for `services.swingset.scratchMaxAgeDays`
   days (default 3). A `KEEP` file at its top exempts it while an investigation
   still needs it.
3. Timer checkpoints (`run_*`) are pruned by policy in `backup/pruning.py`: the
   newest two and anything under two days old stay; abandoned temporary
   directories go after a day. Operator-named checkpoints are removed only by
   name, once the gate they protected has passed.

The timer was deployed on 2026-09-18 as a hotfix built from the exact deployed
source store path with the swingset package pinned, so the running package and
source did not change; only the three unit and script paths were added. The
next ordinary rollout from the repository replaces that system.

## Why

On 2026-09-18 the Mac ran out of disk. The machine held 402 GB: 241 GB in 142
rehearsal scratch copies made between Sep 13 and Sep 18, 97 GB in 17
checkpoints, and 38 GB of Nix store, against a 5 GB live database. Nothing
removed a scratch copy or an old checkpoint, and the machine had no bound, so
the host filled and the guest block device failed with `ENOSPC`. Removing the
scratch and eleven pre-H16 checkpoints freed 266 GB. See the
[investigation](../investigations/2026/worker-disk-exhaustion-2026-09-18.md).

Rebuilding from the working copy or from `main` would have deployed unreviewed
code (the interning migration in progress and the undeployed Step Right
increment), and a path flake changes the recorded source identity, which
invalidates builds. Pinning the deployed package and source avoided both.

## Alternatives

- Only document manual cleanup. Rejected: it was documented as manual and never
  happened.
- Prune operator-named checkpoints by age. Rejected: a held checkpoint guards a
  rollout gate whose duration is not known in advance.
- Put the timer in `/etc/nixos/configuration.nix` on the worker. Rejected: the
  repository would no longer describe the running units.

## Consequences

Rehearsal copies must be sealed as evidence within three days or marked `KEEP`.
Timer checkpoints older than two days are gone unless renamed. The bound must
leave headroom for a migration's write-ahead log and a reclaim rewrite, each
about the database size. The checkpoint pruning was wired into the backup
command the same day; see [D-0152](0152-the-backup-command-prunes-its-own-checkpoints.md).

## Links

- [D-0152](0152-the-backup-command-prunes-its-own-checkpoints.md), the later change that wires the pruning into the backup command
- [Investigation](../investigations/2026/worker-disk-exhaustion-2026-09-18.md)
- [Receipt](../evidence/runtime/worker-disk-bound-2026-09-18/receipt.json)
- [Deployment guide](../../docs/guides/deployment.md), [operation guide](../../docs/guides/operation.md#disk-space)
- [Bounded state plan](../../docs/plans/bounded-state-and-archive.md)
