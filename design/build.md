# Build

`build` materializes the tables from SQLite into Parquet under
`out/data/<table>/`.

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
  published tables (kept locally in `last_published/`), keyed by primary
  key.
- `_meta/manifest.json`: `built_at`, `run_id`, git SHA of this repo,
  parser versions, row counts per table, source snapshot counts, the
  latest event covered, and `schema_version`.
