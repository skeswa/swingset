# Backing up and restoring the worker

A checkpoint must contain the database and every file needed to recover it.
Use the [backup contract](../reference/operations.md#backup-and-restore) for
what is included. These steps describe restoring a complete checkpoint;
[backup sets](#backup-sets-and-the-restore-drill) replace checkpoints once
phase 4 is switched on. Read [current status](../status.md) before changing
the active writer.

## Backup, restore, and a remote-ahead report

Disable timers and stop the former writer before activation. Use a fresh machine:

```sh
orb create nixos:25.11 swingset-restore
orb config set machine.swingset-restore.disk_bytes 68719476736
```

Bound the restore machine to 64 GiB. An unbounded OrbStack machine grows until
the Mac itself is full, which stops every write on the Mac
([D-0133](../../journal/decisions/0133-bound-the-worker-disk-and-remove-rehearsals-eagerly.md)).

Provision `HF_TOKEN` for remote verification. Select a specific private archive
commit and restore its complete checkpoint:

```sh
swingset restore --archive-commit <commit-sha> --state /var/lib/swingset \
  --writer-stopped
swingset doctor --state /var/lib/swingset
```

An already downloaded checkpoint can be selected with
`--checkpoint /path/to/checkpoint` instead. Restore holds the writer lock
through installation and activation. Existing active state is refused; a
marked partial restore can be retried.

Restore verifies files, hashes, SQLite integrity, derived extracts, captured
inputs, baseline and pending candidate directories. `RESTORE_PENDING` blocks
normal mutations until public-head verification succeeds. Verification reads
the public head even if a local receipt exists, then checks it again before
activation. Restore itself never publishes.

A mismatch report names the expected and actual public SHAs. Leave publication
and timers disabled. Restore a newer matching checkpoint, recover the missing
private state, or make an explicit recovery decision about lost evidence.
Do not reset public history or reconstruct private observations from public
Parquet. A public commit after the checkpoint cannot be recovered automatically
without its private evidence.

Inspect the restored `doctor` output and perform a dry cycle. Resume only one
writer. Disposable environments are rebuilt from the frozen lock. A checkpoint
is successful only after the private archive commit is acknowledged.

## Backup sets and the restore drill

A backup set is the phase 4 backup
([backup sets](../reference/state.md#backup-sets),
[D-0232](../../journal/decisions/0232-segmented-backups-with-a-weekly-restore-drill.md)).
It holds the database as zstd parts and, in shared tar segments, only the
files no earlier set sent, so a daily set costs one compressed database
(about 3.3 GB for a 32 GB file) and the day's new files. No file is over
4 GiB, so a set fits the archive's 20 GiB limit. It runs beside the loops.

### Switching to sets

With `services.swingset.backupSegmented = true` the backup timer runs
`swingset backup --segmented --lock-timeout 3600`, with `--local` while
`backupUpload` is off. The set checks its own room before `VACUUM INTO`: the
database's bytes in use (not the file's size, which keeps its free list until
reclaim) plus 20 GB, on the smaller of statfs and the quota. Local sets live
under `/var/lib/swingset/checkpoints/sets/` and `segments/`; each backup
first removes a killed run's scratch, and keeps the newest set and the newest
drilled one. Old `run_*` and named checkpoints stay until an operator removes
them once a set has passed a drill, after an independent check (it deletes
data): `swingset backup --remove-checkpoint` refuses `run_*` names, so a
`run_*` checkpoint is removed with `rm -rf` as root under the hold, with a
receipt.
The architecture plan's phase 4 deploy steps say when to flip the flag.

A set records, instead of failing, an `objects` file that is gone, a release
manifest or partition artifact a retained manifest names that is gone, a pack
row with no `objects` row, and any new file that does not match its digest or
is cut short (the tar stays whole; the file is left out). A missing body,
extract or input bundle still fails the set, after its `VACUUM INTO`.

#### Lost files

An `objects` file is the only copy of an evicted generation's output or a
stubbed body outside the sets. When one is gone, the set puts it back from
the newest local set that holds it, checked against its name (`restored` in
the set's summary). Failing that, the segment of an earlier set that the
transport still holds stands for it (`absent`), so the set is still whole.
The maintain loop's evict does the same each tick (`restored`, `lost`), and
`swingset storage recover-objects` does it by hand; a file no local set holds
is `lost` and holds the guard `loop:maintain evict_failed`
([maintain storage tasks](../reference/operations.md#maintain-storage-tasks)).

- `[backup-incomplete]`: the newest set was written without a file the state
  names (its `backup.json` lists them under `missing`, with why); restoring
  or drilling it refuses. No local set held a good copy. What puts each
  class back:
  - a pack of an evicted generation: a unit that reuses the generation
    computes the same output and rebuilds it; or copy an older set that holds
    it (an uploaded one) into `checkpoints/sets/` and its segments into
    `checkpoints/segments/`, then run `swingset storage recover-objects`;
  - a parse body or build recipe: the same copy, then `recover-objects`;
  - a release manifest or partition artifact: the next build writes it again;
  - a file that does not match its digest: move it aside; the steps above
    put it back.

  `swingset gc --collect` drops only rows nothing names, never a lost pack a
  label still names. Then take a new set.

### Upload

With `backupUpload = true` too, each set goes to the private dataset
`skeswa/swingset-backup-sets` in one commit and the newest 3 remote sets
stay. It is not the checkpoint archive `skeswa/swingset-archive`: the owner
creates it, private, before turning upload on, and the same `HF_TOKEN` needs
write access to it. Then the collector removes every segment no kept set
names and squashes the repository's history, so the removed parts and
segments leave its storage
([D-0234](../../journal/decisions/0234-the-backup-archive-collects-unnamed-segments-and-squashes-its-history.md)).
It refuses, and removes nothing, while the repository holds any path outside
`sets/`, `segments/`, `README.md` and `.gitattributes`: a squash would drop
every old commit, such as the checkpoints `restore --archive-commit` reads.
It writes `checkpoints/remote.json`: the set, when it was uploaded, and the
archive's files and bytes.

- `[backup-stale]`: the newest uploaded set is older than
  `backupMaxAgeSeconds` (36 hours), or none has been uploaded. Read
  `journalctl -u swingset-backup`.
- `[archive-size]`: the sets repository holds over 40 GB (three sets are
  about 15 GB), or its collector failed, for example because the repository
  holds files no set wrote; the set itself was recorded. The next upload
  collects again.

### Restoring a set

Stop the writer as above, then:

```sh
# a local set: point --from at the directory that holds sets/ and segments/
swingset restore --backup-set latest --from /path/to/checkpoints \
  --state /var/lib/swingset --writer-stopped
# an uploaded set, from skeswa/swingset-backup-sets (needs HF_TOKEN)
swingset restore --backup-set <set_id> --state /var/lib/swingset --writer-stopped
```

Restore fetches every file and checks every digest before it places
anything, rebuilds `state.sqlite` from the parts, checks the schema,
`integrity_check` and foreign keys, then follows the protocol above:
`RESTORE_PENDING`, the public-head check and activation. While upload is
off, the sets exist only on the worker; the Mac byte copy is the only copy
off the machine.

### The weekly drill

Each week the maintain loop installs the newest set into
`/var/tmp/swingset-drill-<set>` without activating it or asking the public
hub, checks every digest, `quick_check`, foreign keys and one release's
manifest, and removes the scratch
([restore drill](../reference/operations.md#restore-drill)). It needs the
database's size, the set's files and 20 GB free, and holds the backup's own
lock throughout, so no backup runs beside it. To run one by hand, start
`swingset-maintain.service`: the drill runs when no set passed one in
`drill_every_days` (7) less 12 hours and the tick has 30 minutes left after
the verify sample; once a day overdue it runs before verify.

- `[restore-drill-failed]`: read the maintain record's `drill`, inspect the
  scratch, fix the cause and take a new set. A failed drill keeps its
  scratch for 20 hours without the restored database, which is the size of
  the database's bytes in use and would leave the next set too little room
  (`[backup-failed]` the day after). Touch `KEEP` in the scratch directory to
  keep all of it past the scratch timer, and free that room before 04:00.
- `[restore-drill-failed]` saying "started and never finished": the drill
  was killed, at maintain's 90-minute timeout or 2 GB memory cap; read
  `journalctl -u swingset-maintain`.
- `[restore-drill-stale]` with the guard `loop:maintain drill_no_room`: free
  space for the drill, or it never runs. With `drill_backup_running`: a
  backup held its lock at every drill; check why the backup runs so long.

## Fresh-machine recovery configuration

The `orb-restore` flake configuration installs the CLI and state owner without
collection, backup, or summary timers. Use it for the recovery drill while the
original writer is stopped:

```sh
orb create --cpus 4 --memory 8G nixos:25.11 swingset-restore
orb -m swingset-restore
cd /Users/skeswa/repos/skeswa/swingset
sudo nixos-rebuild switch --flake .#orb-restore --impure
```

Provision the credential separately, then restore the acknowledged archive
commit using a transient service with `EnvironmentFile=/etc/swingset.env`.
Run doctor and a dry cycle before choosing one writer. The recovery machine
remains without timers unless deliberately configured as the new writer.
