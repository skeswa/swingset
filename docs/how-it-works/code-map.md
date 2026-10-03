# Finding the code

Start with the task you want to understand. Five loops run the pipeline,
each tick through [`loops/runtime.py`](../../src/swingset/loops/runtime.py)
(`tick`) and its own module in [`loops/`](../../src/swingset/loops/);
command-line routing lives in [`cli.py`](../../src/swingset/cli.py).

## Pipeline modules

| Task                         | Module       | Start reading                                                                                                                                                                                                                                                                                                                                                    |
| ---------------------------- | ------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Choose due work              | `schedule/`  | [acquisition.py](../../src/swingset/schedule/acquisition.py), [offline.py](../../src/swingset/schedule/offline.py), [release_steps.py](../../src/swingset/schedule/release_steps.py), [fairness.py](../../src/swingset/schedule/fairness.py), [pacing.py](../../src/swingset/schedule/pacing.py)                                                                 |
| Request and save pages       | `fetch/`     | [client.py](../../src/swingset/fetch/client.py), [archive.py](../../src/swingset/fetch/archive.py)                                                                                                                                                                                                                                                               |
| Understand a source's pages  | `sources/`   | [base.py](../../src/swingset/sources/base.py), [registry.py](../../src/swingset/sources/registry.py), then that source's `adapter.py`                                                                                                                                                                                                                            |
| Check source interpretations | `admission/` | [contracts.py](../../src/swingset/admission/contracts.py), [select.py](../../src/swingset/admission/select.py)                                                                                                                                                                                                                                                   |
| Combine source facts         | `project/`   | [map.py](../../src/swingset/project/map.py), [writer.py](../../src/swingset/project/writer.py)                                                                                                                                                                                                                                                                   |
| Resolve person matches       | `link/`      | [service.py](../../src/swingset/link/service.py), [resolution.py](../../src/swingset/link/resolution.py)                                                                                                                                                                                                                                                         |
| Collect historical results   | `history/`   | [catalog.py](../../src/swingset/history/catalog.py), [backfill.py](../../src/swingset/history/backfill.py)                                                                                                                                                                                                                                                       |
| Build a candidate            | `build/`     | [builder.py](../../src/swingset/build/builder.py), [closure.py](../../src/swingset/build/closure.py)                                                                                                                                                                                                                                                             |
| Publish and reconcile        | `publish/`   | [service.py](../../src/swingset/publish/service.py)                                                                                                                                                                                                                                                                                                              |
| Back up and restore          | `backup/`    | [segments.py](../../src/swingset/backup/segments.py) (sets), [restore_set.py](../../src/swingset/backup/restore_set.py), [drill.py](../../src/swingset/backup/drill.py), [transport.py](../../src/swingset/backup/transport.py); before phase 4, [checkpoint.py](../../src/swingset/backup/checkpoint.py) and [pruning.py](../../src/swingset/backup/pruning.py) |

These modules live under `src/swingset/`. For their exact responsibilities,
see [architecture](../reference/architecture.md).

## Shared data and state

`model/` defines records, identifiers, and enum values. `normalize/` cleans
names and source labels. `state/` owns the database, accepted inputs, work
records, and migrations. Start with [db.py](../../src/swingset/state/db.py),
[work.py](../../src/swingset/state/work.py), and
[derivations.py](../../src/swingset/state/derivations.py). Output cutoff lives
there and in `admission/generations.py`; [cutoff.py](../../src/swingset/cutoff.py)
holds its off switch and [recipe_scope.py](../../src/swingset/state/recipe_scope.py)
decides which code each recipe names. Each distinct output row is stored once;
[derivation_payload_migration.py](../../src/swingset/state/derivation_payload_migration.py)
is the one-time fill and check that migration 32 runs.

[retention.py](../../src/swingset/state/retention.py) decides what stays in the
live database: it owns the file closure and the walk behind `gc --plan`, `hold`
and doctor's retention report, and it removes nothing.
[retention_apply.py](../../src/swingset/state/retention_apply.py) is the only
module that removes anything, through `gc --apply` and `gc --reclaim`, from a
written plan under both locks.

Phase 4 moves cold output out of the file.
[storage.py](../../src/swingset/state/storage.py) evicts old generations into
packs ([output_packs.py](../../src/swingset/state/output_packs.py)) and packs
old parse bodies ([body_packs.py](../../src/swingset/state/body_packs.py)), all
stored as content-addressed files by [objects.py](../../src/swingset/state/objects.py).
[collector.py](../../src/swingset/state/collector.py) removes what nothing names.
[disk.py](../../src/swingset/state/disk.py) reads free space as the worker's
disk quota sees it.

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
