# Developing Swingset

The development environment uses Nix and the locked Python dependencies in
`uv.lock`. Run these commands from the repository root:

```sh
nix develop
uv sync --frozen
uv run pytest -q
uv run ruff check .
uv run mypy
uv run swingset doctor --state ./tmp/state
uv run swingset cycle --dry-run --state ./tmp/state
```

The test suite is offline. A dry cycle disables publication; it should not be
used as a substitute for offline tests when you need to avoid source requests.
See [fetching rules](../reference/fetching.md) before any live-source work.

The default test command runs the reviewed core. Use `uv run pytest -q
--full-suite` for core and extended coverage, or give an explicit path to test
one module. The [testing guide](testing.md) owns selection rules, fixture setup
and the measured runtime goal.

## Format and review changes

Formatting only needs mise. Versions are pinned in `mise.toml`:

```sh
mise install
mise run fmt
jj status
jj diff
```

Use Jujutsu (`jj`) for version control. Commit or push only when requested.
See [repository instructions](../../AGENTS.md) for the workflow.

## Find the right module

The [code map](../how-it-works/code-map.md) links tasks to their entry points.
Fixtures live beside source adapters and under `tests/fixtures/`. Captured
fixtures and generated evidence must not be reformatted.

Parser and projector changes need their corresponding version updates. Runtime
recipes also track changed source bytes. Update the relevant reference page
when a change alters a contract; [writing guidance](../writing.md) explains where.

To regenerate enum documentation, run `uv run swingset enums --write` from the
repository root. The output is `docs/reference/enums.md`.
For installation on a worker, use the [deployment guide](deployment.md).
