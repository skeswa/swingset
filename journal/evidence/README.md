# Research evidence

Evidence is grouped by topic, then by investigation or work package. Start
with the linked narrative to understand what a run establishes. Saved files
retain their original bytes, including dates, source pins, and old paths.

## Choose a topic

- [Collection and historical sources](collection/README.md): Build event inventories, examine archived pages, and prepare bounded source collection.
- [Dataset quality](quality/README.md): Inspect a saved dataset and explain missing or conflicting results.
- [Person matches](identity/README.md): Prepare judge evidence and representative identity reviews.
- [Source interpretation checks](admission/README.md): Review a parser’s interpretation before it can replace saved evidence.
- [Worker state and recovery](runtime/README.md): Check migrations, controls, scheduling, checkpoints, and isolated replay.
- [Builds and releases](releases/README.md): Check candidates and correction releases against their supporting evidence.
- [Competition rules](rules/README.md): Inspect the written rules and the sources used to audit their history.

For table columns and reproduction notes, use [artifact formats](formats.md).
For current scripts, use [research tools](../tools/README.md).

## Old paths and frozen records

The top-level `research/` directory was removed. [The path map](paths.json)
records each retained file's old path, current path, and SHA-256 hash. It also
lets tools read old paths inside unchanged, hash-checked manifests. Those paths
are historical references, not duplicate files or symlinks.

Entries marked `local_only` were already ignored local outputs. They moved with
the other evidence but remain outside version control.

The map uses repository-relative paths. Do not edit captured manifests just to
replace a path: doing so would invalidate their hashes. Current tools and new
catalogs use the new locations. A frozen script may still require its original
checkout; its receipt is evidence for that exact script and runtime.

## Add a new run

Use a descriptive directory such as `collection/2026-09-15-calendar-check/`.
Record the date, inputs, code revision, commands, findings, and limits in an
[investigation](../investigations/TEMPLATE.md). Link it from the topic guide.
Keep dependent files together. Store a new run rather than rewriting the old one.
