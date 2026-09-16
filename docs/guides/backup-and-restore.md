# Backing up and restoring the worker

A checkpoint must contain the database and every file needed to recover it.
Use the [backup contract](../reference/operations.md#backup-and-restore) for
what is included. These steps describe restoring a complete checkpoint; read
[current status](../status.md) before changing the active writer.

## Backup, restore, and a remote-ahead report

Disable timers and stop the former writer before activation. Use a fresh machine:

```sh
orb create nixos:25.11 swingset-restore
```

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
