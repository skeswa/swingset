# Build

A build prepares a complete proposed dataset from saved state. That proposed version is a candidate. This page defines what it contains and which checks must pass before publication.

[Reference index](README.md)

## On this page

- [Build inputs](#build-inputs)
- [Immutable contents](#immutable-contents)
- [Review queue](#review-queue)
- [Acceptance cases](#acceptance-cases)
- [History memory and quality metadata](#history-memory-and-quality-metadata)

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
  captured repository/source identity, the exact captured runtime recipe,
  plus the card template hash.

Link output is only one input. Canonical corrections must reach build
when links do not change. Source-event names and dates can change computed review items even
when their map entries stay the same. Every query used to build a published table,
review item, or card field belongs in this dependency list. Revisions
advance only on changed data. Run logs, budget counters, and unchanged
poll timestamps are not published inputs.

H16 selects retained immutable generations at an evidence cutoff. One history
generation anchors its exact event, registry, inventory, and alias dependencies.
A link generation joins only when its dependencies match that anchor. An
unrelated unfinished parse does not block a coherent release. Missing,
unsupported, and incompatible scopes remain disclosed omissions. The older
canonical-table reader keeps its settled-work precondition for callers that
explicitly request that view.

A disposable SQLite spool reconstructs selected rows by column ownership.
It does not modify live canonical tables or copy the changelog. A legacy source
fact may remain only when its owned values match the verified published
baseline under the public column types and no revocation applies. This grants
no new identity join or source admission authority.

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

Before writing candidate files, retain the exact private cutoff proof in a
short transaction. This records inputs, not build completion. The public
manifest contains opaque generation IDs and proof hashes; source locators,
recipes, and private policy notes remain in local immutable storage. Publication
requires both the verified file closure and the durable build-generation receipt.

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
quiet cycles. If it is pruned, a rebuild may repeat the comparison but
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
assertion cannot serve as its own evidence. New default joins remain withheld
until H17 permits expansion; this applies to an empty initial dataset too.

`build/service.py` owns normal and correction-only releases. Normal releases
use the selected dependency closure. Correction-only releases read and hash
the acknowledged baseline, withdraw revoked source scopes and identities, and
rebuild dependent identity fields. They include no new source generation.
The manifest records remaining parse work and findings, withdrawal counts,
the source admission digest, the journal digest and generation, current input
hashes, and the identity policy version. Public assertions carry safe decision
references and acceptance state; private reasons stay private.

The publication boundary repeats the correction, closure, and file checks in
a short admission transaction. Semantic-write fences protect the remote commit;
no SQLite write transaction is held during the network request. Changed or
invalid inputs reject the candidate and retire its unlanded intent. A lost
response is reconciled first: an already-landed commit is acknowledged, then
newly accepted decisions require the next correction. An unlanded intent
cannot submit during restore verification. The receipt records correction
latency; recovered network outcomes report an upper bound.
