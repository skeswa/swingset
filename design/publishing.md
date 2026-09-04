# Publishing to Hugging Face

## Publish (Hugging Face)

## Repos

| Repo | Visibility | Contents |
|---|---|---|
| `skeswa/swingset` | public | published Parquet, README dataset card, `_meta/` |
| `skeswa/swingset-archive` | private | backup of `state.sqlite`, `blobs/`, `last_published/`, and run summaries |

The archive repo is a backup and a reproducibility store, not part of the
run loop. It is private because raw bodies contain names in bulk. It can
be opened later once the suppression path is proven. Restoring a new box
from it is a documented runbook step ([operations](operations.md#backup-and-restore)).

## Layout of `skeswa/swingset`

```
README.md                      dataset card with configs YAML
LICENSE
_meta/manifest.json
data/events/events.parquet
data/contests/contests.parquet
data/rounds/rounds.parquet
data/entries/entries.parquet
data/heats/heats.parquet
data/judges/judges.parquet
data/callback_marks/year=2026.parquet
data/callbacks/callbacks.parquet
data/final_marks/year=2026.parquet
data/placements/placements.parquet
data/dancers/dancers.parquet
data/registry_placements/registry_placements.parquet
data/identity_links/identity_links.parquet
data/link_candidates/link_candidates.parquet
data/review_queue/review_queue.parquet
data/changelog/changelog.parquet
data/snapshots/snapshots.parquet
```

One config per table in the README YAML, `placements` as default:

```yaml
configs:
- config_name: placements
  default: true
  data_files: "data/placements/*.parquet"
- config_name: entries
  data_files: "data/entries/*.parquet"
# ... one per table
```

## Commit strategy

- One `create_commit` per publish, containing every changed Parquet file,
  the README, and the manifest. Atomic on the Hub.
- Publish only when the diff is non-empty. During a busy weekend this is
  a few commits per hour at most. Quiet weeks produce a commit only when
  the registry trickle changes something.
- `parent_commit` is set to the last known head so two overlapping runs
  cannot clobber each other.
- Breaking schema changes happen in place on `main`. Before the first
  commit of the new schema, tag the last commit of the old one
  `schema-v<N>`, bump `schema_version` in the manifest, and add a
  migration note to the card. Anyone who breaks pins the tag. No per-run
  tags; consumers pin by commit SHA via the Hub's history. Until M6 is
  done the schema is pre-1.0 and may change without this ceremony.
- If history grows past a few thousand commits, run
  `super_squash_history` on the archive repo only. The public repo's
  history is kept because it is the point-in-time record.

## Dataset card

Sections, in order: what this is, how to load it (DuckDB, Polars, pandas,
`datasets`), schema summary with links to the tables, update schedule,
correction policy and how to read `identity_links` and `changelog`,
collection method and politeness policy, sources and their terms,
personal data and how to request removal, license, citation, changelog
of schema versions.

License: **ODC-By 1.0** for the dataset. It covers swingset's compilation
and structure and requires attribution. The card states that the
underlying facts are not copyrightable and are not ours. The code in this
repo keeps its existing MIT license.

## Consumer examples (go in the card)

```sql
-- DuckDB
SELECT e.name, p.place, l.name_raw AS leader, f.name_raw AS follower
FROM 'hf://datasets/skeswa/swingset/data/placements/*.parquet' p
JOIN 'hf://datasets/skeswa/swingset/data/events/*.parquet' e USING (event_id)
JOIN 'hf://datasets/skeswa/swingset/data/entries/*.parquet' l ON l.entry_id = p.leader_entry_id
JOIN 'hf://datasets/skeswa/swingset/data/entries/*.parquet' f ON f.entry_id = p.follower_entry_id
WHERE e.year = 2026 AND p.place = 1;
```

```python
import polars as pl
entries = pl.scan_parquet("hf://datasets/skeswa/swingset/data/entries/*.parquet")
confirmed = entries.filter(pl.col("link_status") == "confirmed").collect()
```
