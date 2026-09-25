# D-0203: Back up locally and keep one checkpoint until segmented backups

Recorded: 2026-09-25  
Decided by: agent, 2026-09-25, under D-0175; source: phase 0 of the [architecture plan](../../docs/plans/architecture.md)  
Topic: Worker disk space  
Supersedes: the two kept timer checkpoints of [D-0152](0152-the-backup-command-prunes-its-own-checkpoints.md), while upload is paused  
Superseded by: —

## Decision

Until segmented backups land (architecture phase 4), production backs up
locally only and keeps one timer checkpoint. On orb,
`services.swingset.backupUpload = false` runs `swingset backup --local`, with
a room check for one copy of the state plus 20 GB. `config/sources.toml`
`[retention]` sets `checkpoint_keep_recent = 1` and
`checkpoint_max_age = "0s"`, so each successful backup removes every older
`run_*`; named checkpoints stay. From the phase 1 package on, a local backup
reads its copy back before it prunes; a copy that fails is renamed
`failed-run_*`, at most one is kept, and the backup fails. The watchdog
reports the paused upload daily, a local checkpoint older than 36 hours and
a failed backup. The Mac byte copy, refreshed weekly, is the only copy off
the machine. The retention values apply to every host using this `config/`;
the nix default and the code default are unchanged.

## Why

The archive transport refuses a file over 20 GiB and a checkpoint is about
36 GB, so every upload fails. Checkpoints were pruned only after a successful
upload, so nothing pruned, and the two-copy room check has refused backups
since: the 12:00 UTC check on 2026-09-25 needed 119 GB. A local checkpoint is
the only automatic copy we can make now, and two do not fit beside the
pipeline's 100 GB headroom.

## Alternatives and consequences

A bad new copy could replace the last good one; the read-back closes that,
and until it deploys the Mac copy is the fallback. One checkpoint also means
`LocalCheckpointRecovery` can restore a lost blob or extract only from the
newest copy. The switch deletes about 94 GB of old checkpoints, so it follows
an independent check. The room runs out as the state grows; the plan starts
phase 4 or raises the quota at about 55 GB of state.

## Links

- [Operation guide, local-only backups](../../docs/guides/operation.md#local-only-backups)
