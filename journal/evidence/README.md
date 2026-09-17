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
Append the date, inputs, code revision, commands, findings, and limits to the
existing investigation for that question. Create a new investigation only for
a distinct question. Link it from the topic guide. Store a new attempt rather
than rewriting sealed evidence.

## Keep new evidence small

Retain what is needed to reproduce a finding or verify a gate. Routine test
iterations, exploratory dumps, rebuilt comparison packets and intermediate
screenshots belong in the existing ignored `tmp/` directory or an external
scratch directory. They are not durable evidence and cannot support a completed
gate until the necessary inputs and outputs have been retained.

- Prefer one compact receipt per completed run: source and input hashes,
  commands, exit codes, results, limits and references to required artifacts.
  Include useful failure details in that receipt. Keep failed gate attempts
  distinguishable; do not create a separate narrative for every retry.
- Reference existing retained files by path and SHA-256. Do not copy a prior
  manifest, fixture, receipt or frozen script into each review directory.
  Assemble executable packets in scratch storage when possible. Existing
  reviewed packet contracts still require their complete, exact contents;
  changing those contracts requires implementation and review first.
- For new generated text over **256 KiB**, use lossless gzip before sealing
  hashes. Prefer one compressed bundle for a run's related verbose logs over
  dozens of tiny log files. Verify decompression against the original bytes;
  record both stored and uncompressed hashes and sizes in the compact receipt.
  Consumers must support that representation before it replaces their input.
- Keep files over **1 MiB**, even compressed, in a verified external archive.
  Record its location, hashes and retrieval instructions in a compact receipt.
  Keep the smallest real fixtures that cover the tested behavior. Large source
  bodies required for provenance remain evidence, not disposable diagnostics.
- Keep candidate summaries in the
  [candidate history](../../docs/reference/candidate-history.md), current state
  in [status](../../docs/status.md), and detailed outcomes in the existing
  investigation. Link between them instead of repeating full histories.

Future source-freeze tooling should distinguish executable/build/test inputs
from historical evidence. Whole-repository inventories currently include earlier
evidence in each later freeze. A smaller inventory needs an explicit inclusion
list and review of all gate dependencies before use; existing candidates and
validation contracts remain unchanged. This tooling change is still pending.

Run `mise run evidence-size` before retaining a new bundle. It reports tracked
file sizes and exact duplicates without generating another evidence file.
It fails if any tracked file, including files outside evidence, exceeds 1 MiB.
It never deletes files and does not establish operational acceptance.

These rules apply to new output. Do not recompress, remove or rewrite existing
sealed evidence in place. Moving it later requires a lossless archive, verified
retrieval, and updated readers and references before removing the old paths.
A local scratch path or hash alone is not a durable archive. The migration below
covers the selected large files; preserve other bundles and all operational gates.

## Restore archived large files

The [2026-09-17 scrub receipt](runtime/history-scrub-2026-09-17/receipt.json)
records the verified external archive of all tracked contents over 1 MiB.
Thirteen paths were removed from thirteen unpublished revisions; published
`main` was unchanged. Exact local copies remain at their original paths under
narrow `.gitignore` rules. This is a repository-history change, not a change to
any source capture, production runtime, or published dataset.

The archive is currently on this machine at
`/Users/skeswa/archives/swingset-history-scrub-20260917T210700Z/`.
It contains `before.tar.gz` (tracked files and full version-control backup),
`large-blobs.tar.gz` (deduplicated removed contents), `inventory.json.gz`
(historical paths and hashes), and `restore_current.py`. It is not an off-site
backup; copy the directory to durable storage before retiring this machine.

For a new checkout, obtain that archive directory and verify its hashes against
the receipt. Restore the required fixture and operational evidence before tests
or replay:

```sh
python3 /path/to/archive/restore_current.py /absolute/path/to/swingset
mise run evidence-size
```

The restore verifies the blob archive and each restored body, requires the exact
ignore rules, and refuses to replace different existing files. The raw DCN index
fixture exceeds the limit even after gzip; it remains external. Do not skip or
weaken its tests when the archive is missing. Source-freeze builders must include
required restored fixtures explicitly or restore them before validation; a
tracked-files-only copy now omits these archived inputs. Existing frozen
candidates and their validation receipts retain their original bytes.
