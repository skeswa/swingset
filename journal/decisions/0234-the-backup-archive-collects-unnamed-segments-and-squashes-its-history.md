# D-0234: The backup archive collects unnamed segments and squashes its history

Recorded: 2026-10-03  
Decided by: agent, 2026-10-03, under D-0175; source: the phase 4 review, before `backupUpload` turns on  
Topic: Worker disk space  
Supersedes: "segments never removed" in [D-0232](0232-segmented-backups-with-a-weekly-restore-drill.md), in upload mode  
Superseded by: —

## Decision

Backup sets upload to their own private dataset, `skeswa/swingset-backup-sets`,
which the owner creates before `backupUpload` turns on; never to the
checkpoint archive `skeswa/swingset-archive`. After each upload, the backup
removes in one commit every segment that no kept set's `backup.json` names,
then squashes that repository's history to one commit
(`super_squash_history`), so the removed database parts and segments leave
its storage. It refuses, removing nothing, while the repository holds any
path outside `sets/`, `segments/`, `README.md` and `.gitattributes`; that is
`[archive-size]`. It writes what the archive holds
(sets, segments, files, bytes, any error) to `checkpoints/remote.json`.
`backup_objects` rows of segments the archive no longer holds go, so their
files are sent again. The watchdog reads the record: `[backup-stale]` past
`backupMaxAgeSeconds`, `[archive-size]` past 40 GB or on a failed
collection. A directory transport (`--destination`) collects the same way,
without history.

## Why

A file deleted in a commit stays in the repository's history, so every
daily database part (about 3.3 GB, up to three a day Friday to Sunday) would
stay in the archive forever, and nothing alerted on an upload that stopped.
The sets repository is a backup, not a record: its history has no reader.
The checkpoint archive's history does: `restore --archive-commit <sha>` reads
old checkpoint commits, and a squash there would delete every one of them
and leave the old `checkpoint.json` and `_transport/checkpoint.tar` counting
toward the size alert. Hence a repository of its own, and the refusal as a
guard against pointing the transport at the wrong one. Three kept sets and
their segments are about 15 GB.

## Alternatives and consequences

Rotating to a new repository each month also frees the history, but changes
the transport's name and its rows each time. A squash rewrites history the
owner can see on the Hub. The archive's storage limits are **unverified**.

## Links

- [Backup and restore guide](../../docs/guides/backup-and-restore.md)
