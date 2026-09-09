# Build

`build` materializes the tables from SQLite into Parquet under
`candidates/<candidate_id>/data/<table>/`.

- Deterministic sort order per table (by primary key) so unchanged data
  produces byte-identical files.
- Written with PyArrow using `use_content_defined_chunking=True` and
  `write_page_index=True` so Hugging Face's Xet storage uploads only
  changed chunks.
- Row groups sized around 128 MB uncompressed. Most tables are one file.
  `callback_marks` and `final_marks` are partitioned by year:
  `data/callback_marks/year=2026.parquet`.
- Schema enforcement: each table has a PyArrow schema in
  `swingset/model/schema.py`. Build fails on any mismatch. Enum values
  are checked against the vocabulary.
- Invariants checked at build, failing the run if violated:
  - every `entry_id` in marks exists in `entries`;
  - every placement has a `place` in 1..N with no gaps per round;
  - `entries.wsdc_id` set only when `link_status` in (`confirmed`, `probable`);
  - a bib per role per event maps to at most one `wsdc_id`;
  - no suppressed `wsdc_id` or name appears anywhere.
- Suppression is applied here, last, from `overrides/suppressions.csv`:
  the matching `entries`, `judges`, and `dancers` rows keep their
  structural columns and get null names, null `wsdc_id`, null city, and
  `link_status = suppressed`. Their `link_candidates` rows are removed.
  Raw bodies in the private archive are untouched.
- `review_queue` is rebuilt from scratch each build from the current
  state, so resolved items vanish without bookkeeping.
- `changelog` is computed by diffing the new tables against the last
  published tables (kept locally in `baseline/`), keyed by primary
  key.
- `_meta/manifest.json`: candidate id, captured `built_at` and `run_id`,
  repository commit id (or an explicit source identity for an uncommitted
  build), parser/projector/linker versions, row counts,
  source snapshot counts, latest event covered, and `schema_version`;
  also the content hash, build fingerprint, expected parent commit,
  input bundle hash, and hashes of all published data and card files.
  Raw override contents stay private in the captured input bundle.

## Build inputs

Build reads all published inputs directly. Its fingerprint contains:

- `canonical`, `dancers` (including registry placements), `links`,
  `findings`, `snapshots`, `source_events`, and `source_event_map`
  revisions;
- hashes of every captured file read by suppression, computed review
  items, and the card, including suppressions, source configuration,
  event aliases, and source URL overrides;
- schema, package, extractor, parser, projector, and linker versions,
  captured repository/source identity, plus the card template hash.

Link output is only one input. Canonical corrections must reach build
when links do not change. Source-event names and dates can change computed review items even
when their map entries stay the same. Every query used to build a published table,
review item, or card field belongs in this dependency list. Revisions
advance only on changed data. Run logs, budget counters, and unchanged
poll timestamps are not published inputs.

Build runs only with no pending parse, project, or link work, using one
consistent database view and the captured input bundle. It cannot
publish half of a vocabulary migration or half of an alias move. Handled
parse failures preserve last-good observations and appear as findings;
unhandled projection or link failures keep work pending and block build.

Candidate reuse requires **both** this fingerprint and the baseline
commit against which its changelog was calculated. The candidate's
`BUILT` record is the completion record; no SQLite completion row can
outlive its directory. A matching baseline already published from these
inputs needs no rebuild. Otherwise reuse a complete candidate with the
same pair, or build one. Deleting a disposable candidate makes that work
due again without database repair.

## Immutable contents

Allocate candidate metadata once before writing files. Write into a
temporary directory, validate invariants and file hashes, then make
`BUILT` durable last. Incomplete directories are never reusable. The
candidate's data, card, manifest, and changelog are immutable after
`BUILT`; only publication receipts are added later.

Separate the stable **content hash** from the manifest hash. The content
hash covers published tables other than changelog, the card, and semantic
metadata such as schema and implementation versions. It excludes run
ids, build times, the expected parent, local revision counters, and the
changelog derived from the baseline. Equal content hashes mean no new
public belief: do not publish a commit merely because a candidate has a
new run id. The manifest hash identifies exact immutable output for
publication recovery; it is not the no-change test.

`BUILT` records the content comparison outcome. A complete no-change
candidate for the same input/baseline pair prevents repeated work on
quiet cycles. If it is pruned, a rebuild may repeat the comparison but
still creates no remote commit. A fresh allocation can have different
run metadata; data files and semantic content must be deterministic.
Reusing a candidate preserves every byte, including its metadata.

The changelog is the baseline's history plus the current delta. Build
never compares changelog against itself to generate more changelog rows.
Promotion, not building or a dry run, advances the history. Every table
is present from the first publish, empty where v1 has no data, so the
card configs and consumer examples are complete from day one.

## Review queue

Build combines open findings with ambiguous links, unsupported contests,
and unmatched source events computed from the current database and
captured overrides. Finding kinds include `conflict`, `invalid_response`,
and `unknown_enum`, as well as the existing review kinds. Item ids and
opening provenance derive from their evidence; build time alone never
changes a row. Resolved items disappear without a separate queue writer.

## Acceptance cases

- A projection-only change triggers build with link and snapshot
  revisions unchanged; a cross-check-only finding does the same.
- Build suppression B as a dry run against baseline A, publish
  suppression C, then restore B without other input changes. B must
  rebuild against C and retain C's changelog history.
- Reusing the same complete candidate preserves exact bytes. Rebuilding
  unchanged semantic inputs with fresh run metadata produces the same
  content hash and no extra commit.
- A partial projection or link queue prevents a new build; a handled
  parser failure can publish its finding alongside last-good rows.
