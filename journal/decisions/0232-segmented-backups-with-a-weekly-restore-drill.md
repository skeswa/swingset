# D-0232: Segmented backup sets replace full checkpoints, each verified by a weekly drill

Recorded: 2026-10-02  
Decided by: agent, 2026-10-02, under D-0175; source: phase 4 of the [architecture plan](../../docs/plans/architecture.md), adopted by the owner on 2026-09-25  
Topic: Worker disk space  
Supersedes: the one local checkpoint of [D-0203](0203-back-up-locally-and-keep-one-checkpoint-until-segmented-backups.md) and the checkpoint knobs of [D-0152](0152-the-backup-command-prunes-its-own-checkpoints.md), once the nix flag flips  
Superseded by: [D-0234](0234-the-backup-archive-collects-unnamed-segments-and-squashes-its-history.md) in part: the archive collects unnamed segments

## Decision

A backup is a set (`backup --segmented`): the database as zstd parts of at
most 4 GiB, an index of every file the state needs, and tar segments of at
most 4 GiB that hold only files no earlier set sent. `backup.json` is the
commit point. Upload may resume (`backupUpload`): one archive commit per set,
the newest 3 remote sets kept, segments never removed. Local mode writes the
same layout under `checkpoints/`. Local pruning keeps the newest set and the
newest drilled one, and the segments they name. Each set is checked by a
weekly offline restore drill (WP7) that installs it in scratch.
`restore --backup-set` installs a set and activates it like a checkpoint.
Until the flag flips, the full checkpoint stays the default.

## Why

The archive refuses files over 20 GiB and a checkpoint is about 36 GB, so
upload has been off since D-0203. No set file is over 4 GiB. A daily set
sends the database and the new files only. A backup that was never restored
is not known to work; the drill restores one every week.

## Alternatives and consequences

Two local sets mean two compressed database copies on the worker. The room
check and the watchdog move to sets in WP7. A set taken while maintain's
collector removes an object fails and the next run retries.

## Links

- [State reference: backup sets](../../docs/reference/state.md#backup-sets)
