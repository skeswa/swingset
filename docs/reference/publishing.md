# Publishing to Hugging Face

Publication makes an accepted candidate visible on Hugging Face. The baseline is the last acknowledged public version. This page owns commit ordering and recovery when local and remote state differ.

[Reference index](README.md)

## On this page

- [Repos](#repos)
- [Layout of `skeswa/swingset`](#layout-of-skeswaswingset)
- [Commit strategy](#commit-strategy)
- [Candidate and baseline](#candidate-and-baseline)
- [Publication gate](#publication-gate)
- [Publication acceptance cases](#publication-acceptance-cases)
- [Dataset card](#dataset-card)
- [Consumer examples (go in the card)](#consumer-examples-go-in-the-card)

## Repos

| Repo                          | Visibility | Contents                                                                        |
| ----------------------------- | ---------- | ------------------------------------------------------------------------------- |
| `skeswa/swingset`             | public     | published Parquet, README dataset card, `_meta/`                                |
| `skeswa/swingset-archive`     | private    | complete checkpoint described in [operations](operations.md#backup-and-restore) |
| `skeswa/swingset-backup-sets` | private    | segmented backup sets (phase 4, D-0234); history squashed, nothing else in it   |

The archive repo is a backup and a reproducibility store, not part of the
run loop. It is private because raw bodies contain names in bulk. It can
be opened later once the suppression path is proven. Restoring a new box
from it is a documented runbook step ([operations](operations.md#backup-and-restore)).

## Layout of `skeswa/swingset`

```
README.md                      dataset card with configs YAML
LICENSE
_meta/manifest.json
_meta/verdict.json             publication gate verdict (D-0225)
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

One config per table appears in the README YAML. `placements` is the default once it
has rows. A calendar-only bootstrap instead defaults to `events`, because the Hugging
Face viewer returns an error when it tries to stream an empty default Parquet config.
The next card switches the default to `placements` when results arrive:

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
- Publish only when the candidate content hash differs from the baseline. During a busy weekend this is
  a few commits per hour at most. Quiet weeks produce a commit only when
  the registry trickle changes something.
- `parent_commit` is the candidate's recorded expected parent, checked
  against the baseline before each attempt. It is never silently updated
  to whatever head the Hub currently returns.
- Breaking schema changes happen in place on `main`. Before the first
  commit of the new schema, tag the last commit of the old one
  `schema-v<N>`, bump `schema_version` in the manifest, and add a
  migration note to the card. Anyone who breaks pins the tag. No per-run
  tags; consumers pin by commit SHA via the Hub's history. Until M6 is
  done the schema is pre-1.0 and may change without this ceremony.
- If history grows past a few thousand commits, run
  `super_squash_history` on the archive repo only. The public repo's
  history is kept because it is the point-in-time record.

## Candidate and baseline

A candidate is one immutable proposed dataset version. Its reuse key is
`(build_input_fingerprint, baseline_commit)`, because changelog is a
function of both. `candidate_id` identifies that allocation independently
of the cycle that later publishes it. `baseline` points to the last
acknowledged public version stored locally.

The candidate directory is the publication journal. There is no second
publication state machine in SQLite. Its files are:

| Record             | Meaning                                                                                                                  |
| ------------------ | ------------------------------------------------------------------------------------------------------------------------ |
| `BUILT`            | Contents complete and validated; records reuse key, candidate metadata, content hash, manifest hash, and expected parent |
| `PUBLISHING`       | Durable intent to publish these exact contents under that expected parent                                                |
| `PUBLISHED`        | Remote commit SHA acknowledged for these contents                                                                        |
| `baseline` symlink | This candidate is the locally adopted public baseline                                                                    |

Data and metadata files are immutable after `BUILT`. Records are written
with temporary files and atomic rename, with files and containing
directories made durable before proceeding. Promotion atomically replaces
the baseline symlink after `PUBLISHED` is durable. The commit SHA has
one receipt, `PUBLISHED`; there is no duplicate `COMMIT` file.

A candidate is pending from `PUBLISHING` until baseline promotion, even
if `PUBLISHED` already exists. Only one may be pending. Publish checks
that the candidate's recorded baseline equals the current baseline,
writes `PUBLISHING`, then submits all changed files and deletions in
one commit with the recorded parent. Its message names the candidate id
and manifest hash. An acknowledgment writes `PUBLISHED` and promotes.
The content comparison from [build](build.md#immutable-contents) happens
before any intent record or remote mutation.

Before the first publish, bootstrap explicitly records the repo's
initial head (or empty-repo state) as the expected parent and uses an
empty logical dataset as baseline. A Hugging Face head containing only
`.gitattributes` and its exact generated ODC-By license card is empty for
this purpose. Any other README, file, manifest, or data rejects bootstrap;
an existing published dataset must be restored. Retry handling also covers
the first publish with no baseline symlink. Bootstrap keeps the initial
head as the commit parent, so it does not omit concurrency checks.

Reconcile runs before any new build, including when no inputs changed:

| Pending state                                                                        | Action                                                                                                   |
| ------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------- |
| `PUBLISHED` exists                                                                   | Finish local promotion without a network request                                                         |
| No receipt; remote head matches the candidate id, manifest hash, and expected parent | Verify the remote manifest and file hashes, write the receipt, then promote                              |
| No receipt; remote head still equals expected parent                                 | Real run: retry this candidate. Dry run: report pending, skip new build and publish, leave intent intact |
| No receipt; head is anything else                                                    | Fail with the expected and actual heads; do not rebuild, promote, or overwrite the remote                |

`swingset publish`, including `--dry-run`, reconciles first and obtains
its candidate through build. Standalone build and publish require earlier
work queues to be drained; they report pending work instead of bypassing
it. Use cycle to drain that work. All publication entry points reject
`RESTORE_PENDING`.

The release loop runs the cycle's build and publish steps once an hour
([scheduling: loops](scheduling.md#loops)). `swingset release --publish`
reconciles first, then builds and publishes as the cycle does.
`--dry-run`, the default, has no hub: it neither reconciles nor publishes,
and only builds. A release tick settles a publication admission that a dead
process left active as uncertain, so reconcile decides it. While a
closure-mode publication is active its fence refuses every write outside the
control tables, so the other loops pause: each checks before a unit or request
starts, and a unit write that still meets the fence retries in 60 s
([D-0215](../../journal/decisions/0215-loops-share-the-state-under-a-reader-writer-lock.md)).
A partitioned candidate is admitted as `publication_narrow`: its fence covers
only what its check reads, and acquire, plan and derive keep running
([state: publication fences](state.md#publication-fences)).

**What publication checks.** Inside the publication admission, every
candidate's file closure, manifest hash and baseline commit must still hold,
with its durable build receipt and the input bundle unchanged. Then by mode:

| `release_policy.mode` | Also checked                                                                                                                                                                                                                                                                                   |
| --------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `partitioned`         | The [release manifest](build.md#partitioned-releases) file and every artifact it names hash to their digests; the manifest names this candidate's token and baseline; no revocation since the build; the correction token (journal, decisions, admission policies) is unchanged. No graph walk |
| `closure`             | The pinned closure validates against the database, and the correction token is unchanged (the baseline built before phase 3 is closure mode)                                                                                                                                                   |
| `settled`             | No pending work, and the revisions are unchanged                                                                                                                                                                                                                                               |

A partitioned candidate's `PUBLISHED` receipt records `manifest_digest` and
the manifest's `evidence_cutoff`. A changed artifact file refuses publication
and rejects the candidate; the next build writes a new one
([D-0224](../../journal/decisions/0224-releases-from-partition-artifacts.md)).

Reading the head is allowed in a dry run. A dry run creates no Hub
commit; it may complete an already acknowledged local promotion. A
publication request with a lost response is reconciled before retrying.
The correctness condition is one **remote commit** per candidate, not
one HTTP attempt: a dropped request may require another attempt.

After reconciliation, recompute the build reuse key against the promoted
baseline. An old dry-run candidate for the same input fingerprint but
a different baseline is unusable. Never change its expected parent or
reuse its stale changelog.

Keep the last five disposable candidates, plus the baseline and any
pending candidate. Backup staging pins any referenced candidate until
that checkpoint completes. Incomplete build directories are disposable;
missing baseline or pending artifacts are errors, never an excuse to
forget publication intent. [Restore](operations.md#backup-and-restore)
verifies remote state separately because its checkpoint may be older
than subsequent public commits.

## Publication gate

Every candidate must be at least as good as the baseline, measured
([D-0225](../../journal/decisions/0225-publish-only-strictly-better-releases.md)).
The builder compares the candidate with the baseline on these measures:

| Measure                           | Counts                                                                                                                                                                                     |
| --------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| The table's name                  | Rows of `events`, `contests`, `rounds`, `entries`, `judges`, `callback_marks`, `callbacks`, `final_marks`, `placements`, `dancers`, `registry_placements`, `heats` (manifest `row_counts`) |
| `confirmed_links`                 | `identity_links` rows with status `confirmed` and a WSDC id                                                                                                                                |
| `linked_entries`, `linked_judges` | `entries` and `judges` rows with a WSDC id                                                                                                                                                 |
| `events_with_placements`          | Distinct `placements.event_id`                                                                                                                                                             |

Not measured: `review_queue`, `changelog`, `coverage`, `snapshots`,
`link_candidates`. A measure that goes down needs a row in
`overrides/release_decreases.csv` with the same measure and baseline value
whose `candidate_value` is at most the candidate's: the row is a floor, so a
later build with a few more rows still passes and a larger decrease does not
([how to write one](../guides/data-corrections.md#approving-a-decrease)).
Without one the verdict is `refused`, with one reason per decrease:
`decrease:<measure> <from>-><to>`. A table the candidate lacks counts as 0.
With no baseline the verdict passes. The loops capture the file without
parsing it; a malformed one approves nothing and refuses the release with
`overrides_invalid:release_decreases.csv:<line>: <error>` first among the
reasons.

The verdict lists every measure as baseline and candidate values, each
decrease, the overrides it used and the reasons. The builder writes it to
`_meta/verdict.json`, named and hashed in the candidate manifest like every
file, and publishes it with the data. It is left out of the content hash, so
an identical rebuild is still unchanged. The cycle and the release loop put
it in the run summary as `verdict` (as `correction_verdict` for a
correction) and on the candidate's `release_manifests` row. A dry run
records it too.

`publish` reads the verdict after the baseline check and before any intent.
A refused verdict returns state `refused` with the reasons: nothing is
written or submitted, the candidate is not rejected, and the tick is not a
failure. The release loop counts it in `refused`. A verdict file that no
longer matches its manifest hash refuses (`verdict_changed_after_build`). A
candidate built before the gate has no verdict; publication evaluates it
against the baseline with no overrides. A correction-only candidate records
its decreases but is exempt (`exempt: "correction"`): the suppression or
identity rows that asked for it are the approval. A pending publication
(`PUBLISHING`) already passed and is not gated again.

## Publication acceptance cases

Inject failure before and after intent, remote commit, receipt, and
promotion. Restart with both unchanged and newly changed inputs. Assert
one remote commit for the original candidate, correct baseline history,
and a new candidate only when new inputs require it. Include a request
that never reaches the remote and one whose response is lost after the
commit lands. A third-party head must fail without mutation.

A dry run with a pending intent may read the head but creates no commit.
An acknowledged candidate promotes without any network request. A dry
run with no pending candidate builds only. A quiet cycle with no due
watch, pending work, changed input, or publication intent makes no
network request.

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
