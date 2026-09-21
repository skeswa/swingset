# D-0192: A failed backup waits for the next timer run

Recorded: 2026-09-22  
Decided by: agent, 2026-09-22, under D-0183  
Topic: Operations  
Supersedes: —  
Superseded by: —

## Decision

The backup unit no longer restarts on failure. A failed backup triggers the
watchdog, which logs and alerts, and the next attempt is the next day's timer
run. The retention policy keeps pruning timer checkpoints after a successful
backup as before.

## Why

The backup writes a complete local checkpoint, about 26 GB today, before it
builds the transport archive. On 2026-09-22 the archive exceeded the 20 GiB
single-file transport limit because the live database had grown to 24 GB
after a day of re-derivation. The unit had `Restart=on-failure` with no start
limit, so it retried every minute, and each retry wrote another full copy:
six copies in forty minutes, 156 GB, until the disk was full. Every cycle
then failed with "database or disk is full" for eleven hours. The watchdog
saw three failed cycles but could not append to its alert log on a full disk.

Two follow-ups are queued: a transport that splits the archive across files
so a checkpoint over 20 GiB can still upload, and a watchdog that raises an
alert through `logger` first, before it touches the disk. Until the first
lands, remote checkpoints fail while the archive is over the limit, and the
local copy is the only backup; the status page says so.

## Links

- [Receipt](../evidence/runtime/disk-full-recovery-2026-09-22/receipt.json)
- [Operation guide, disk space](../../docs/guides/operation.md#disk-space)
