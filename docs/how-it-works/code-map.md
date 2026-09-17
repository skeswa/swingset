# Finding the code

Start with the task you want to understand. The pipeline entry point is
[`run_cycle`](../../src/swingset/schedule/cycle.py); command-line routing
lives in [`cli.py`](../../src/swingset/cli.py).

## Pipeline modules

| Task                         | Module       | Start reading                                                                                                  |
| ---------------------------- | ------------ | -------------------------------------------------------------------------------------------------------------- |
| Choose due work              | `schedule/`  | [cycle.py](../../src/swingset/schedule/cycle.py), [fairness.py](../../src/swingset/schedule/fairness.py)       |
| Request and save pages       | `fetch/`     | [client.py](../../src/swingset/fetch/client.py), [archive.py](../../src/swingset/fetch/archive.py)             |
| Understand a source's pages  | `sources/`   | [base.py](../../src/swingset/sources/base.py), then that source's `adapter.py`                                 |
| Check source interpretations | `admission/` | [contracts.py](../../src/swingset/admission/contracts.py), [select.py](../../src/swingset/admission/select.py) |
| Combine source facts         | `project/`   | [map.py](../../src/swingset/project/map.py), [writer.py](../../src/swingset/project/writer.py)                 |
| Resolve person matches       | `link/`      | [service.py](../../src/swingset/link/service.py), [resolution.py](../../src/swingset/link/resolution.py)       |
| Collect historical results   | `history/`   | [catalog.py](../../src/swingset/history/catalog.py), [backfill.py](../../src/swingset/history/backfill.py)     |
| Build a candidate            | `build/`     | [builder.py](../../src/swingset/build/builder.py), [closure.py](../../src/swingset/build/closure.py)           |
| Publish and reconcile        | `publish/`   | [service.py](../../src/swingset/publish/service.py)                                                            |
| Back up and restore          | `backup/`    | [checkpoint.py](../../src/swingset/backup/checkpoint.py)                                                       |

These modules live under `src/swingset/`. For their exact responsibilities,
see [architecture](../reference/architecture.md).

## Shared data and state

`model/` defines records, identifiers, and enum values. `normalize/` cleans
names and source labels. `state/` owns the database, accepted inputs, work
records, and migrations. Start with [db.py](../../src/swingset/state/db.py),
[work.py](../../src/swingset/state/work.py), and
[derivations.py](../../src/swingset/state/derivations.py).

The [data model](../reference/data-model.md) describes published tables.
[Local state](../reference/state.md) describes what the worker keeps between runs.

## The rest of the repository

| Directory                             | Purpose                                                   |
| ------------------------------------- | --------------------------------------------------------- |
| `tests/`                              | Offline tests and test helpers                            |
| `config/`                             | Source settings and host request limits                   |
| `overrides/`                          | Reviewed event mappings, identity decisions, and removals |
| `nix/`                                | Worker service and host configuration                     |
| `docs/`                               | Explanations, task guides, reference, and active plans    |
| `journal/`                            | Research narratives, decisions, and dated outcomes        |
| `journal/tools/`, `journal/evidence/` | Research scripts and retained evidence                    |

Use the [development guide](../guides/development.md) to run checks.
