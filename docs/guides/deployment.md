# Installing the NixOS worker

This guide describes the existing OrbStack setup. Read [current status](../status.md)
and preserve any operating hold. Local development steps are in the
[development guide](development.md). The PostgreSQL/Dokploy target has a
[separate plan](../plans/postgres-migration/README.md).

## Development and VM setup

```sh
orb create --cpus 4 --memory 8G nixos:25.11 swingset
orb config set machine.swingset.disk_bytes 274877906944
orb -m swingset
cd /Users/skeswa/repos/skeswa/swingset
sudo nixos-rebuild switch --flake .#orb --impure
systemctl list-timers 'swingset-*'
journalctl -u swingset-cycle -f
```

The machine is bounded to 256 GiB of disk (`disk_bytes`), and the restore
machine to 64 GiB. Without a bound OrbStack lets a machine grow until the Mac
itself is full, which stops every write on the Mac, not only the worker. On
2026-09-18 the unbounded machine reached 402 GB, almost all of it rehearsal
copies under `/var/tmp` and old checkpoints, and the Mac ran out of space.
OrbStack enforces the bound at once as a btrfs quota on the machine's
subvolume; `df` inside the machine still shows the whole shared image, so read
usage against the bound from `orb info swingset` on the Mac. Inside the bound, the
`swingset-scratch-clean` timer removes idle rehearsal scratch and the backup
command prunes old timer checkpoints; see the
[operation guide](operation.md#disk-space).

A switch that migrates the state database may leave the file larger than its
contents: a migration that drops a table, such as schema 32, frees pages to
SQLite's free list without shrinking the file. Run `swingset gc --reclaim` after
such a switch, as the [operation guide](operation.md#disk-space) says, or the
old size stays on disk and in every backup.

Use `jj status` before evaluating newly created files with a Git-backed flake.
`path:.#orb` also works during initial bootstrapping. The host configuration
imports `/etc/nixos/configuration.nix`, which includes the generated container,
network, user, certificate, and OrbStack settings. Importing only
`orbstack.nix` omits the container and network setup. Do not make the generated
configuration import the flake host file back into itself.

The Mac and Linux share the checkout. Use a separate venv inside Linux:

```sh
export UV_PROJECT_ENVIRONMENT=/home/skeswa/swingset-venv
nix develop
uv sync --frozen
uv run python -c 'import pyarrow, duckdb, scipy, rapidfuzz, selectolax, httpx'
```

The service uses `/var/lib/swingset/venv` and its own uv cache. Both can be
rebuilt. Code and Python dependencies come from the flake and frozen lock;
overrides are captured from `services.swingset.overridesDir` every cycle.
The manifest records the clean repository revision or an explicit uncommitted
source-store identity. Local development falls back to a digest of the Python
sources. A code identity change invalidates the build; parser and projector
changes still require their respective version bumps. Captured runtime recipes
also detect changed bytes when a manual version stays the same.
