# Installing the NixOS worker

This guide describes the existing OrbStack setup. Read [current status](../status.md)
and preserve any operating hold. Local development steps are in the
[development guide](development.md). The PostgreSQL/Dokploy target has a
[separate plan](../plans/postgres-migration/README.md).

## Development and VM setup

```sh
orb create --cpus 4 --memory 8G nixos:25.11 swingset
orb -m swingset
cd /Users/skeswa/repos/skeswa/swingset
sudo nixos-rebuild switch --flake .#orb --impure
systemctl list-timers 'swingset-*'
journalctl -u swingset-cycle -f
```

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
