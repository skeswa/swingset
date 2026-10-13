# Build

A build prepares a complete proposed dataset from saved state. That proposed version is a candidate. This page defines what it contains and which checks must pass before publication.

[Reference index](README.md)

## On this page

- [Build inputs](#build-inputs)
- [Immutable contents](#immutable-contents)
- [Review queue](#review-queue)
- [Acceptance cases](#acceptance-cases)
- [History memory and quality metadata](#history-memory-and-quality-metadata)
- [Partition artifacts](#partition-artifacts)
- [Partitioned releases](#partitioned-releases)
  - [Streamed assembly](#streamed-assembly)

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
  - callback round and judge references resolve; published callback sums and
    yes/alternate/no counts agree with retained marks, within float tolerance;
  - event start dates do not exceed their end dates;
  - no event ended before the history start, 2010-01-01 by default and
    `history_start` in `config/sources.toml` ([backfill](backfill.md#the-start-date-rule));
    an event's end date decides, then its start date, then its year, and
    an event with none of these is not rejected;
  - every placement has a `place` in 1..N with no gaps per round;
  - `entries.wsdc_id` set only when `link_status` is `confirmed`;
  - a bib per role per event maps to at most one `wsdc_id`;
  - no suppressed `wsdc_id` or name appears anywhere.

  Each check but the last is one function in `build/builder.py` (`CHECKS`),
  so a [partition artifact](#partition-artifacts) runs the same checks on its
  own rows.

- Suppression is applied here, last, from `overrides/suppressions.csv`:
  matching entries and judges retain their structural rows and marks while
  personal names, initials, location, and default IDs become null. Entries
  receive `link_status = suppressed`. Name-bearing entry and judge IDs become
  deterministic opaque IDs, with matching changes to marks, chief-judge and
  partner references. Ordinary bib-based structural keys remain intact.
  Suppressed dancers and registry placements are omitted because their WSDC
  numbers are primary keys; nulling several keys would create invalid rows.
  Candidate and identity assertions for suppressed subjects or WSDC numbers
  are removed. Placement IDs and points depending on a suppressed identity
  become null, and combined confirmation flags are cleared. This corrects the
  earlier contract that proposed keeping dancer rows with null primary keys.
  Generated and historical changelog streams pass through the same suppression
  policy. Reopened history scans retain only affected keys and name fragments
  until discovery stops changing. A bounded pass limit fails closed instead of
  emitting partially scrubbed history. Streaming output then omits private
  changes without altering the order of surviving history. Operator reasons
  and private notes are not copied into public output. Raw bodies in the
  private archive are untouched.
- `review_queue` is rebuilt from scratch each build from the current
  state, so resolved items vanish without bookkeeping.
- `changelog` is computed by diffing the new tables against the last
  published tables (kept locally in `baseline/`), keyed by primary
  key.
- `_meta/manifest.json`: candidate id, captured `built_at` and `run_id`,
  repository commit id (or an explicit source identity for an uncommitted
  build), parser/projector/linker versions, row counts,
  source snapshot counts, the `history_start` the build enforced, latest
  event covered, and `schema_version`;
  also the content hash, build fingerprint, expected parent commit,
  input bundle hash, and hashes of all published data and card files.
  Raw override contents stay private in the captured input bundle.

## Build inputs

Build captures one read snapshot and the input bundle. Its fingerprint contains:

- `canonical`, `dancers` (including registry placements), `links`,
  `findings`, `snapshots`, `source_events`, and `source_event_map`
  revisions;
- hashes of every captured file read by suppression, computed review
  items, and the card, including suppressions, source configuration,
  event aliases, and source URL overrides;
- schema, package, extractor, parser, projector, and linker versions,
  captured repository/source identity, the build's code scope and
  `recipe/env`
  ([D-0207](../../journal/decisions/0207-scope-code-recipes-by-import-closure.md)),
  plus the card template hash.

Link output is only one input. Canonical corrections must reach build
when links do not change. Source-event names and dates can change computed review items even
when their map entries stay the same. Every query used to build a published table,
review item, or card field belongs in this dependency list. Revisions
advance only on changed data. Run logs, budget counters, and unchanged
poll timestamps are not published inputs.

The default release reads partition artifacts
([partitioned releases](#partitioned-releases)). The closure path below runs
only with `[release] path = "closure"`, or before schema 35.

H16 selects retained immutable generations at an evidence cutoff
(`build/closure.select`). Anchors are added in order: every year's generation
in year order (its pointer when made by the cutoff, else its newest one then),
each bringing its events, calendars, inventory, resolutions and alias
dependencies;
then every source event's and every dancer's pointer. An anchor is skipped
when its walk reaches a generation an earlier anchor selected differently, or
a project scope's generation other than that scope's own anchor. Since output
cutoff no longer checks the walk (D-0224), a kept year can reach a superseded
event or dancer through a kept inventory; that year is skipped and its events
are withheld with reason `year_anchor_conflict`, never published stale. A
skipped source event or dancer keeps what the years selected. Without a year
anchor nothing is selected. The retired `project/history/all` is neither selected nor listed
(plan F22). A link generation joins only when its dependencies match what the
anchors selected. An unrelated unfinished parse does not block a coherent
release. Missing,
unsupported, and incompatible scopes remain disclosed omissions. A row's event
is resolved through its parents, kept or rejected. When an event's kept rows
would still leave a reference the builder refuses (a mark, callback or heat
without its entry, round or judge; a placement without its entry), the whole
event is withheld and listed in `row_omissions` with reason
`dangling_reference`; a row whose event cannot be named is dropped alone. One
bad event never fails the release
([D-0199](../../journal/decisions/0199-contain-build-failures-and-pace-global-units.md)). The older
canonical-table reader keeps its settled-work precondition for callers that
explicitly request that view.

A disposable SQLite spool reconstructs selected rows by column ownership.
It does not modify live canonical tables or copy the changelog. A legacy source
fact may remain only when its owned values match the verified published
baseline under the public column types and no revocation applies. This grants
no new identity join or source admission authority.

An `events` header row is the one exception that keeps its event
([D-0251](../../journal/decisions/0251-a-rejected-legacy-header-publishes-the-baseline-row.md)).
When a legacy header differs from the baseline and no admissible header
holds the event, the baseline's own `events` row stands in
(`closure_rows._baseline_header`), so the event's accepted results still
publish. Every column comes from the baseline row, including its provenance
(`source`, `sources`, `snapshot_id`, `parser_version`, `run_id`,
`first_seen_at`, `last_seen_at`), except `coverage_tier`, which starts at
`index_only` for the year's patch, and the freshness columns
(`scope_status`, `evidence_observed_at`), which assembly computes. The
stand-in's snapshot and parser join the partition's support; a revoked one
withholds the event. A later admissible header replaces the stand-in whole.
The stand-in takes the rejected row's status in two cases only: `unknown` to
`registry` for a held occurrence of the same `wsdc-*` series, and `registry`
to `unknown` for a series that is not a registry series
([D-0254](../../journal/decisions/0254-a-registry-occurrence-publishes-its-accepted-listing-header.md)).

When the baseline lacks the event, the partitioned build tries the event's
listings (D-0254). If the event is a held occurrence of a `wsdc-*` series and
a listing mapped to it (its resolution's `source_event_map`, the
`source_events` row from the listing's `project/source_index` generation)
cites an `accepted` snapshot and parser, a header is made from it: the
registry's event id, series, month, `held` and status, the listing's name,
dates, source and provenance, and no place, website or live window. The
widest span, then the newest snapshot, wins. The year's tier patch applies
to it. Listing rows are header candidates only: they are never merged or
checked as rows. A legacy result row of an event that took a placeholder's
listing (D-0253) is kept when the same row under the placeholder's ids, the
id the map generates for that listing, equals the baseline's row; the
sidecar counts these as `legacy_moved_retained`. The closure path does
neither. Otherwise an event the
baseline lacks is withheld as before, and a rejected result row
(a contest, entry or placement) still withholds its event: mixing a
baseline result row into current ones could break the event's structure. The
sidecar counts stand-ins as `baseline_header_republished` and listing
headers as `listing_header_published`.

Candidate reuse requires **both** this fingerprint and the baseline
commit against which its changelog was calculated. The candidate's
`BUILT` record and verified file closure establish artifact completion.
A retained H15 materialized pointer is current only while that closure verifies;
the pointer alone cannot establish completion. A matching baseline already published from these
inputs needs no rebuild. Otherwise reuse a complete candidate with the
same pair, or build one. Deleting a disposable candidate makes that work
due again without database repair.

H15 selects the build derivation inside the same read snapshot as its rows.
After the candidate files and `BUILT` are durable, a short transaction rechecks
the baseline and captured inputs, then records the immutable generation and
artifact hashes. H16 checks the pinned closure rather than newer source pointers.
Changed corrections, contracts, captured inputs, or baseline still reject the
unpublished candidate. A crash before that transaction can leave reusable files but no completed
generation. Missing or corrupt candidate files force a fresh candidate even
when its manifest and `BUILT` remain. The recorded generation describes local
materialization; only a publication receipt establishes published progress.

After the candidate files are durable, and before the build generation is
completed, retain the exact private cutoff proof in a short transaction. A
failed attempt retains nothing; a candidate left without its proof is rejected
and rebuilt, never reused. The build generation's recipe names the proof by
its public manifest, not a copy (D-0199). This records inputs, not build
completion. The public
manifest contains opaque generation IDs and proof hashes; source locators,
recipes, and private policy notes remain in local immutable storage. Publication
requires both the verified file closure and the durable build-generation receipt.

Completion validates the pinned closure before output, after output rows, and
before certification. Within that transaction it may reuse a full validation
only after rechecking the supplied manifest and hashing the exact supporting
database evidence. A changed digest requires full validation again. This
operation-local proof retains no decoded source payloads and does not replace
baseline, input-bundle, correction-policy, or candidate-file checks. The ordinary
write deadline remains unchanged.

Reuse a release while selected evidence, corrections, and material health
status remain unchanged. Public health advances once per UTC day, initially,
and immediately when material status changes. A successful unchanged poll
does not cause a new release. The actual evidence cutoff remains fixed in a
reused candidate. Reusing the acknowledged baseline creates no remote commit.

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
quiet release ticks. If it is pruned, a rebuild may repeat the comparison but
still creates no remote commit. A fresh allocation can have different
run metadata; data files and semantic content must be deterministic.
Reusing a candidate preserves every byte, including its metadata.

The changelog is the baseline's history plus the current delta. Build
never compares changelog against itself to generate more changelog rows.
When comparing pre-H16 coverage, its year/source/via key maps to the
corresponding year-scope key. Each old year remains distinct, its old payload
is retained, and new scope fields appear as updates. Stored historical
changelog records keep their original keys.
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

## History memory and quality metadata

Prior changelog rows stream through a bounded merge with the current delta.
The merge preserves the original JSON composite-key ordering, including nullable
fields; it does not materialize the full history as Python dictionaries.
The card reads history counts lazily. The initial full-history benchmark covered
2,673,569 rows with about 347 MiB maximum resident memory.

`calendar_horizon` is the greatest event end date across metadata rows.
`latest_event_covered` is the greatest end date among events with placements.
Neither upgrades override month ranges into verified event dates. The card
reports result coverage, entry link statuses, and review kinds separately.

The v2 build includes `coverage` with source and transport counts, year
acceptance, date precision, and an unknown expected-round denominator until
enumeration is checked. It validates month-only events using `event_month`;
day precision requires both dates. A retained round is partial sheet coverage
until the index and its complete round set have passed closure checks.

Default entry and judge WSDC IDs accept confirmed links only. Probable
identity decisions remain in `identity_links` and `link_candidates` for
review. H10 rechecks exact source-owned printed IDs, registry agreement in the
selected release tables, current decisions, and source support. A former
assertion cannot serve as its own evidence. A new default entry ID must also
pass the reviewed H17 rules of
[D-0248](../../journal/decisions/0248-publish-new-identity-links-that-pass-the-h17-review-rules.md)
([identity linking](identity-linking.md#release-acceptance-of-new-links-h17));
new manual and judge IDs stay withheld. This applies to an empty initial
dataset too.

`build/service.py` owns normal and correction-only releases. Normal releases
use the selected dependency closure. Correction-only releases read and hash
the acknowledged baseline, withdraw revoked source scopes and identities, and
rebuild dependent identity fields. They include no new source generation.
The manifest records remaining parse work and findings, withdrawal counts,
the source admission digest, the journal digest and generation, current input
hashes, the admission policies and revocations, and the identity policy
version. Public assertions carry safe decision references and acceptance
state; private reasons stay private. A revert (`--revert-to`) reads a former
published candidate's tables instead of the baseline's and records
`reverted_from`. [Publishing](publishing.md#corrections-and-rollback) owns
when the release loop builds a correction.

The release loop's hourly tick starts a build
([scheduling: loops](scheduling.md#loops)). `build_interval_seconds` admits one
at most every 6 hours. In the release loop every build and every correction
holds `heavy.lock` exclusive, so derive starts no unit until it ends (F10): a
whole or closure build peaks near 6.3 GB, and a partitioned one keeps the
lock while `[release] streamed_lock` is true (the default) until the worker
measures it under 2 GB beside derive. The loop does not build with under `release_min_free_bytes` (30 GB) of
disk free. A `BuildError` or `ClosureError` fails the tick without raising.

The publication boundary repeats the correction, closure (for a partitioned
candidate, manifest and artifact; [publishing](publishing.md#candidate-and-baseline)),
and file checks in a short admission transaction. Semantic-write fences protect the remote commit;
no SQLite write transaction is held during the network request. Changed or
invalid inputs reject the candidate and retire its unlanded intent. A lost
response is reconciled first: an already-landed commit is acknowledged, then
newly accepted decisions require the next correction. An unlanded intent
cannot submit during restore verification. The receipt records correction
latency; recovered network outcomes report an upper bound.

## Partition artifacts

Stage 1 of a [partitioned release](#partitioned-releases) (architecture plan
phase 3). Each release build runs it first. The command
`swingset partitions build [--only <partition>]` runs it alone;
`partitions list` and `partitions show <name>` read the rows. `build` prints
counts, bytes written, seconds and peak memory.

Selection reads the current pointers (`derivation_scopes`) only: no live
rows, no dependency walk, no unfinished generation, no retired kind. A
dancer generation joins the batch of the WSDC id its `dancers` rows carry
(its scope id except after a lookup redirect), where the year's patch of its
placements lands.

**Consistency.** An event partition is built only when every unit it reads
whole (its event, resolution and link) is current, so the link's identity
rows were computed from that event generation and those dancers, and the
event from that resolution. Otherwise it keeps the artifact it last built
(the build report's `kept_not_current` counts kept artifacts; the row records
`kept_not_current_since`, which the next rebuild clears and the watchdog's
`[partitions-stale]` reads past 48 hours), or is withheld as
`units_not_current` with none; a kept artifact whose support was revoked
since is withheld (`source_revoked`). The content digest also names the event
generation each link recorded reading. When two event partitions both hold a
listing's `source_event_map` row (a map move built mid-drain), the one whose
units are current keeps it, else the one holding the event the live map
names; the other is withheld as `listing_claimed_elsewhere` under an inputs
digest no reuse matches, so the claim is contested again each build and the
loser is built once the other claim goes.

A prior artifact is reused or kept only when it is intact: its sidecar
hashes to its digest and every file it lists has the recorded sha256
(`artifact_store.intact`, about 73 MB read a build). A damaged one is built
again, and the write replaces the damaged directory. `partitioned.prepare`
checks every built artifact once more after stage 1 and, if one was damaged
meanwhile, runs stage 1 again at once (`damaged_rebuilt`).

| Partition         | Holds                                                                                                                                                                                                                        |
| ----------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `event/<E>`       | The `events` row merged from the calendar, inventory and `resolution/E` generations, then the year's tier patch; `event/E`'s rows; `link/E`'s patches and identity rows; `resolution/E`'s `source_event_map` rows            |
| `event/<E1>+<E2>` | Events joined because a row of an event generation names more than one (the `support_closure._EventScopes` rule: its `event_id` and the events of the contest, round, entry, judge or placement it names in that generation) |
| `dancers/<k>`     | `k = wsdc_id // 512`: dancer and registry placement rows, each placement's `event_id` patched from the year generation of its month                                                                                          |
| `year/<Y>`        | The year generation's `coverage` rows                                                                                                                                                                                        |

Rows merge in `closure_rows.reconstruct`'s rank order and rule (a patch needs
a base row; processing columns come from each generation). Within a partition
the build then closes what `reconstruct` closes: identity rows whose subject
is absent go, and a placement's dancer follows its entry's.

**Inputs digest.** It covers `(scope, generation_id, output_digest)` of each
generation read whole; `(scope, table, key, payload sha256)` of each row taken
from a generation many partitions share (calendar, inventory, year), in merge
order; the observation sets each generation last recorded reading; each
snapshot and parser its rows name, with its standing and
accepted source generations; a digest of the live observations of each pair
no generation recorded; the revocation digest; `recipe/env`,
`recipe/code/build`, `policy/history_start` and the partition modules' bytes;
the history start; and the baseline commit and manifest hash when a row's
support is legacy. An unchanged digest keeps the artifact and rewrites no row.
Processing columns (`run_id`, `first_seen_at`, `last_seen_at`) are not in it,
so a kept artifact keeps those of the build that last changed its facts.

**Checks.** Each failure withholds that partition, with its reason and
detail in `release_partitions`, and nothing else:

| Reason                                                                                                                               | When                                                                                                                                                                             |
| ------------------------------------------------------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `source_revoked`, `source_support_unavailable`                                                                                       | A row's snapshot and parser has a revoked source generation, or none accepted and the row differs from the baseline (a header row with a baseline row is replaced by it instead) |
| `dangling_reference`                                                                                                                 | A mark, callback, heat or placement names an entry, round or judge the partition lacks                                                                                           |
| `generation_output_digest_mismatch`                                                                                                  | A generation read does not match its row count and output digest                                                                                                                 |
| `partition_error`                                                                                                                    | Selection (`events_of`), extraction, a check, the Arrow conversion or the write raised; the detail has the error's class and message                                             |
| `units_not_current`, `listing_claimed_elsewhere`                                                                                     | See consistency above                                                                                                                                                            |
| `invalid_enum`, `invalid_event_dates`, `missing_reference`, `callback_counts_disagree`, `invalid_placements`, `unsupported_identity` | The builder's checks above, on the partition's rows                                                                                                                              |

Support comes from the accepted source generations, never a clock (plan
F24): accepted means an accepted admission decision, the current contract and
no report failures. It is matched per observation, as `closure_support` does
(D-0022): a pair is `accepted` only when each observation its rows came from
(those its generations last recorded reading, else the snapshot's live
ones) is held, with the same scope, kind, seq, payload and extract version, by an
accepted generation of that snapshot and parser. A generation's last read
is its newest equivalence's dependency set, else its own: a re-run with
unchanged output keeps the generation but can read relabelled observations
(D-0241), and the set saved with the generation then names labels no accepted
generation holds. The sidecar's `support`
holds the result; the manifest's support is its union. The closure path
credited only the generation a unit had selected, so a row from an older,
still admitted snapshot of a re-fetched page counted as legacy there (R4's
four events). A dancer whose rows lack support leaves its batch and is
listed in the sidecar's `withheld_subjects`; entries and links that name it
are closed at assembly.

**Files.** One Parquet file per non-empty table, in the public schema
(`source_event_map` has its own), sorted by primary key, and `sidecar.json`
(partition, inputs and their digest, every generation read, withheld
subjects, counts, and each file's rows and sha256) under
`state/partitions/<aa>/<digest>/`. The digest is the sidecar's sha256. A
write goes to `partitions/.tmp-<id>/`, is fsynced and renamed; a crash leaves
no row. The maintain loop's collector removes what nothing names
([state: partition artifacts](state.md#partition-artifacts)).

Phase 3b, which deletes `closure_rows`, first moves `_NEUTRAL`,
`_REFERENCES`, `_legacy_matches` and its baseline loader, which the partition
modules import.

## Partitioned releases

`[release] path` in `config/sources.toml` picks how a release is assembled
([D-0224](../../journal/decisions/0224-releases-from-partition-artifacts.md)).
It is read from the accepted bundle at build time and is not a derivation
input, so a change re-queues nothing.

| `path`                  | Build                                                                                                                                                                                                                                |
| ----------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `partitioned` (default) | Stage 1, the manifest, then the [streamed assembly](#streamed-assembly): one partition at a time                                                                                                                                     |
| `whole`                 | Stage 1, the manifest, then every artifact loaded into `BuildInput` and the builder run unchanged                                                                                                                                    |
| `closure`               | The phase 2 [closure path](#build-inputs); also what runs before schema 35. After the phase 3 switch it builds a release with no events (F30) and the gate refuses it; `whole` is the fallback. A correction-only build is unchanged |

**Manifest.** `release-manifest-v1`, canonical JSON:

| Field                           | Holds                                                                                                        |
| ------------------------------- | ------------------------------------------------------------------------------------------------------------ |
| `built`, `withheld`             | Sorted `[partition, artifact_digest]` and `[partition, reason]` from `release_partitions` after stage 1      |
| `overrides_digest`              | The hashes of the override files assembly reads: `identity_overrides.csv`, `suppressions.csv`                |
| `code`                          | `recipe/env` and `recipe/code/build`                                                                         |
| `baseline_commit`               | The acknowledged baseline's commit                                                                           |
| `support_digest`, `revocations` | The union of the artifacts' support (`[snapshot, parser, state, accepted generations]`) and the revocations  |
| `correction_token`              | `identity_policy.correction_token` with the support digest, admission policies and revocations as admission  |
| `evidence_cutoff`               | The newest `created_at` among the generations and accepted source generations the built artifacts name (F24) |

No clock, run id or path, so two attempts on the same pointers and inputs
write the same bytes. The manifest lists stage 1's verdicts only: a
partition the assembly withholds (`assembly_check:<check>`) stays under
`built` here and in `release_partitions`; the release record names it in
`partitions.assembly_withheld` and `release_policy.partitions` counts it. It is stored as `state/partitions/manifests/<digest>.json`
(digest = sha256 of the bytes) with a `release_manifests` row that names the
candidate it built. Build code: `build/manifest.py` (format, files, tokens),
`build/partitioned.py` (stage 1, manifest, assembly input).

**Whole assembly.** Every built artifact's rows load into public tables. Across
partitions, as `closure_rows._close` does: a dancer an artifact withheld, or
any dancer of a withheld `dancers/<k>` batch, leaves the entries, judges and
identity rows that name it; a registry placement whose event is not in the
release loses its `event_id`; a placement's dancer follows its entry's. The
key check (`assembly.close_keys`) then closes every other WSDC id the rows
name without a `dancers` row: entries and judges lose the join, identity
links and candidates go, and placements follow. `release_policy.partitions`
counts `identity_of_withheld_dancer_closed`,
`identity_of_absent_dancer_closed` and
`registry_association_outside_release`. The closure path has no such check,
so it can publish a join to a dancer the release lacks.
`snapshots` are the snapshots the artifacts' support names; `review_queue`
comes from open findings, as in the closure path. Then the pipeline after
reconstruction runs unchanged: `close_revoked_support`, the identity policy,
suppressions, `enrich_coverage` (support from the artifacts, withheld
partitions as omissions), event coverage, and `build_candidate`. Coverage,
snapshots and the review queue carry per-build columns and may differ from
the closure path's; the fact tables match on settled state
(`tests/build/test_release_paths.py`).

### Streamed assembly

`build/assembly.py` publishes the `whole` assembly's rows while Python holds
one partition at a time. DuckDB runs with `memory_limit = 1GB`, two threads and
its file cache off, and spills under `state/tmp/assembly-<id>/`, which the
build removes; a killed build's directory goes at the next build once it is a
day old.

| Step                  | What it does                                                                                                                                                                                                                                                                                                                            |
| --------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Pass 1 (DuckDB)       | Over the artifacts' key columns: the release's events; the WSDC ids of entries, judges, identity links, candidates and placements with no `dancers` row; registry `event_id`s outside the release; every (snapshot, parser) the rows name, each checked for revocation once. Revoked dancers and registry ids                           |
| Revoked partitions    | An event partition with a revoked row is closed first (`Revocations.omitted_events`), so the events left are known before any row is written                                                                                                                                                                                            |
| Dancer batches        | Key check of registry events, then revocation (`close_registry`, `withdraw`)                                                                                                                                                                                                                                                            |
| Event partitions      | Key checks, revocation, then the identity policy (`IdentityPolicy.apply`) with that partition's baseline entries and judges and the registry facts naming its placements' events                                                                                                                                                        |
| Suppression           | Only with a selector in `suppressions.csv`: `learn` sweeps every staged dancer, entry and judge to a fixed point, then each partition is transformed                                                                                                                                                                                    |
| Annotation and checks | Per partition: scope status and evidence time (`coverage.annotate`), then the builder's `CHECKS` (after the suppression pass when there is one). A failure withholds that partition (`assembly_check:<check>`): its omission reaches coverage, its events' registry associations are detached, entries naming its dancers lose the join |
| Release level         | Snapshots the support names, the review queue from findings, coverage from the year fragments plus scope rows from SQL aggregates (`Assembly.fact_counts`), and event coverage rows                                                                                                                                                     |
| Candidate             | Each table sorted by primary key from DuckDB into public Parquet (marks by event year), identity counts from SQL, the changelog, the card from lazy tables                                                                                                                                                                              |

Rows go to staging files between steps, one row group per partition. The
changelog is a per-table DuckDB full outer join of the new files with the
baseline's, each key's pair compared by `changelog.change_rows` (the
`whole` comparison), sorted on disk and merged with the baseline changelog.
Suppression convergence (up to 32 passes) re-streams the partitions; with
`suppressions.csv` empty it is one pass. Rows equal `whole`'s; file bytes
need not (`tests/build/test_assembly.py` compares with
`compare_candidates.py`).

Memory: no unit of work holds more than one partition, one batch or one
release-level list (snapshots, review queue, coverage, events) in Python;
`Assembly.meter` counts them. The support items coverage reads stream from
the manifest's support with only `recipe.context.source` of each accepted
source generation (F26: whole recipes were 3.9 GB of the rehearsal's 6.75 GB).
A synthetic release of 200,000 placements and 400,000 marks peaks under
600 MB, and the whole partitioned build (stage 1, support, assembly) over
40,000 accepted recipes of 4 kB under 900 MB, where loading whole recipes
took 1.4 GB (`tests/build/test_assembly_memory.py`, extended). The final
sort of the largest table is DuckDB's. The summary's `partitions` key adds
`assembly_seconds` and `assembly_peak_rows`.

The `whole` path applies the same per-partition checks
(`assembly.withhold_invalid`) after its suppressions and before coverage,
grouping rows by the events they name and dancers by batch.

**Candidate.** `release_policy` has `mode = "partitioned"`, `path`,
`manifest_digest`, `evidence_cutoff`, `withheld` (count, digest, count by
reason), `partitions` and `build_trigger`, instead of the public closure. The
build generation's context names `release_manifest`, the correction token and
the build trigger; its only dependency is the manifest, and the scope is
current only while the candidate files and the manifest file verify.

**Reuse.** The trigger is the manifest without its baseline commit, the path,
public health and the bundle. An unchanged manifest reuses the candidate
within the daily health bucket; the acknowledged baseline stays quiet after
its own publication. A new path builds afresh.

**Completion.** After the files are durable, the write rechecks the baseline,
the bundle and the correction token, then records the build generation. A
token that moved during stage 1 or assembly fails the build before it is
written.
