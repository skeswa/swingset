# Worker disk exhaustion and its bound

Date: 2026-09-18. Purpose: explain why the Mac ran out of disk, what was
removed, and what now prevents a repeat. Decision: [D-0133](../../decisions/0133-bound-the-worker-disk-and-remove-rehearsals-eagerly.md).
Receipt: [worker-disk-bound-2026-09-18](../../evidence/runtime/worker-disk-bound-2026-09-18/receipt.json).

## What happened

The production worker is the OrbStack machine `swingset`. At 05:46 UTC OrbStack
logged repeated `write failed … No space left on device` on the machine's block
device; the guest journal shows its previous boot ending at 05:00 UTC and
OrbStack's daemon restarting at 05:56 UTC. The Mac's data volume had 5.7 GiB
free of 926 GiB, and the OrbStack image was 402 GB.

Inside the machine, `du` attributed the space as follows.

| Location                                   | Bytes  | What it is                                                            |
| ------------------------------------------ | ------ | --------------------------------------------------------------------- |
| `/var/tmp/swingset-*`                      | 241 GB | 142 rehearsal scratch directories, each a 4 to 9 GB copy of the state |
| `/var/lib/swingset/checkpoints`            | 97 GB  | 17 full checkpoints                                                   |
| `/nix`                                     | 38 GB  | Nix store                                                             |
| live state, candidates, operations, caches | 12 GB  |                                                                       |

The scratch copies date from Sep 13 (about 60 GB), Sep 14 (19 GB), Sep 15
(9 GB), Sep 16 (24 GB), Sep 17 (108 GB) and Sep 18 (8.5 GB). Nothing held them
open, nothing in the state directory linked to them, and no tmpfiles rule aged
them. Retained receipts mention their paths as history, not as inputs.

## What was removed

With owner approval: every `/var/tmp/swingset-*` directory except
`swingset-schema29-restore-20260917-004` (a restored copy of held checkpoint
004, now marked `KEEP` for the bounded-state plan's measurement step), and the
eleven checkpoints `h11-before-h12-20260913` through `h15-before-h16-20260913`
and `run_20260909T042445Z` through `run_20260909T144629Z`. That freed 238 GB of
scratch and 28 GB of checkpoints. After `fstrim`, the guest used 143 GB, the
OrbStack image 142 GB, and the Mac had 265 GiB free.

## What prevents a repeat

- `orb config set machine.swingset.disk_bytes 274877906944` bounds the machine
  to 256 GiB, enforced immediately as a btrfs quota (`btrfs qgroup show` reports
  a 256 GiB maximum against 138 GB referenced). `df` inside the machine does not
  show the bound; `orb info swingset` does. The restore machine is bounded to
  64 GiB.
- The `swingset-scratch-clean` timer (05:00 daily) removes idle scratch after
  three days unless a `KEEP` file is present. It was deployed the same day as
  system `mni8kwh2472nrfljz1v6kydrvx7f2m70-nixos-system-swingset-lxc-25.11.20260630.b6018f8`,
  built from the deployed source store path with the swingset package pinned;
  `nix store diff-closures` against the previous system lists only the script,
  service and timer. The machine was restarted afterwards; the hold, timers and
  units were intact, and the timer's first run kept the marked directory.
- `backup/pruning.py` prunes timer checkpoints by policy and removes named ones
  only by name. Wiring it into `swingset backup` is pending.

## Limits

The scratch cleaner runs as root on the real `/var/tmp`; the service units use
`PrivateTmp` and never see those directories. The hotfix system is not
reproducible from the repository alone; the scratch flake that built it is
disposable. The size cap in the bounded-state plan is separate from this
machine bound and is still to be set.
