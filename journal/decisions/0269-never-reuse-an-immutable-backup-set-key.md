# D-0269: Never reuse an immutable backup set key

Recorded: 2026-11-04  
Decided by: agent under D-0172, 2026-11-04; source: the owner's instruction to implement stage 1  
Topic: Backup identity and crash recovery  
Supersedes: —  
Superseded by: —

## Decision

A new backup volume gets a random 64-bit namespace. Each attempted set
reserves a monotonically increasing counter with an atomic, durable write
before producing its timestamped set id. Retries always get a new id.
Remote objects use conditional creation and refuse different bytes at an
existing key. Existing retained set ids keep their names.

Before installing a local set, write a pending ownership record containing
the actual database UUID and manifest digest. Remove it only after the
verified set's database receipt commits. The next holder of the common
backup lock removes an unrecorded matching set, or confirms a matching
committed receipt. Conflicting ownership or hashes block cleanup.

## Why

A process can die after uploading an immutable object or renaming its
local set but before recording success. Timestamp-only retries could reuse
a remote key for different bytes. Durable reservation prevents that within
a volume; a new namespace prevents reuse after a fresh-volume restore.
The pending record also makes immediate cleanup safe without guessing a
process's age or removing another backup's work.

## Links

[Publication and backup ordering](../../docs/plans/postgres-migration/4-hosting.md#publishing-and-backups).
