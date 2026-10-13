# Local state

Local state is what the worker keeps between runs: saved evidence, database rows, unfinished work, and release files. This page defines how that state changes and what must survive a restart.

[Reference index](README.md)

## On this page

- [SQLite schema](#sqlite-schema)
- [Registry verification (H1)](#registry-verification-h1)
- [State directory](#state-directory)
- [Invalidation](#invalidation)
- [Read recorder](#read-recorder)
- [Identity decision state](#identity-decision-state)
- [Acceptance cases](#acceptance-cases)
- [Requirement inventory in shadow (H11)](#requirement-inventory-in-shadow-h11)
- [Isolated work attempts (H12)](#isolated-work-attempts-h12)
- [Work queue](#work-queue)
- [Event completion persistence (H14 extension)](#event-completion-persistence-h14-extension)
- [Derivation generations (H15)](#derivation-generations-h15)
- [Mapping per source, resolution per event (plan F19)](#mapping-per-source-resolution-per-event-plan-f19)
- [Link read sets (plan F20)](#link-read-sets-plan-f20)
- [Partition artifacts](#partition-artifacts)
- [Objects and packs](#objects-and-packs)
- [Backup sets](#backup-sets)
- [Accepted source generations (H7)](#accepted-source-generations-h7)
- [Durable controls (H13)](#durable-controls-h13)
- [Publication fences](#publication-fences)
- [Retention](#retention)

## SQLite schema

One file, WAL mode, `foreign_keys = ON`, schema versioned by numbered
SQL migrations under `state/migrations/`. Names match the published
tables where a table is published. The [schema history](schema-history.md) lists
what each numbered migration changes and links to its exact SQL.

Internal tables:

| Table                                         | Key columns                                 | Purpose                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| --------------------------------------------- | ------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `meta`                                        | `key`                                       | `schema_version`, `installed_at`, current input bundle hash                                                                                                                                                                                                                                                                                                                                                                                             |
| `runs`                                        | `run_id`                                    | `started_at`, `finished_at`, `dry_run`, `summary_json`, `loop` (the owning loop; NULL for the CLI and for cycle runs from before phase 5b); maintain compacts a loop row's summary to a stub after 30 days                                                                                                                                                                                                                                              |
| `hosts`                                       | `host`                                      | `next_allowed_at`, `paused_until`, `pause_reason`, `pause_streak`, `robots_sha256`, `robots_fetched_at`, `robots_status`                                                                                                                                                                                                                                                                                                                                |
| `operator_pauses`                             | `scope_kind`, `scope_id`                    | operator-requested all, host, source, or requirement-kind pause; stable pause ID, actor, reason, revision, creation time and nullable expiry; separate from automatic host pauses                                                                                                                                                                                                                                                                       |
| `host_budget`                                 | `host`, `day`                               | `requests`, `bytes`                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| `cursors`                                     | `name`                                      | `value`; durable bootstrap and new-id probe positions and miss counts; `registry_probe_started_at`, `registry_probe_last_completed_at`, `registry_probe_next_at` preserve freshness and cadence across restarts                                                                                                                                                                                                                                         |
| `watches`                                     | `watch_id`                                  | every column in [scheduling](scheduling.md#watches) plus `fingerprint`, `extract_version`, `priority`, `created_by_snapshot_id`, `parent_watch_id`, `ever_ok`, current observation snapshot id                                                                                                                                                                                                                                                          |
| `snapshots`                                   | `snapshot_id`                               | every column in [fetching](fetching.md#archive) plus `via`, `archive_url`, `captured_at`, `observed_at`, `headers_json`, `classification`, `extract_status`, `extract_sha256`, `parse_status`, `parsed_at`, `extract_version`, `parser_version` (versions last attempted)                                                                                                                                                                               |
| `archive_captures`                            | `source`, `url`, `timestamp`                | `digest`, `status`, `mimetype`, `length`, `queried_at`, `cdx_query_id`; the Wayback CDX index we hold per source, so capture selection can be redone offline ([backfill](backfill.md#the-wayback-transport))                                                                                                                                                                                                                                            |
| `observations`                                | `observation_id`                            | `watch_id`, `snapshot_id`, `kind`, `scope_kind` (`source_event`, `dancer`, `source_index`, `calendar`), `scope_id` (a source reference such as `eepro:asc2025`, never a canonical id), `seq`, `extract_version`, `parser_version`, `payload_json`; indexed by `watch_id` and by (`scope_kind`, `scope_id`)                                                                                                                                              |
| `source_event_map`                            | `source`, `source_ref`                      | `event_id`, `match_method` (`name_date`, `alias`, `override`), `match_confidence`; the projection that resolves observation scopes to events; each event's rows are written by its `project/resolution/<event_id>` unit from the proposals that name it                                                                                                                                                                                                 |
| `revisions`                                   | `name`                                      | monotonically increasing counter per set (`observations`, `source_event_map`, `source_events`, `canonical`, `dancers`, `links`, `findings`, `snapshots`), bumped in the writing transaction                                                                                                                                                                                                                                                             |
| `pending_work`                                | `stage`, `unit_kind`, `unit_id`             | `enqueued_at`; coalesced work for parse, project, and link; presence means unfinished                                                                                                                                                                                                                                                                                                                                                                   |
| `work_queue`                                  | `stage`, `unit_kind`, `unit_id`             | overlay on `pending_work` and derivation currency: `state` (`ready`, `leased`, `waiting_input`, `quarantined`), `attempts`, `error_class`, `input_fingerprint`, lease owner and expiry, `next_attempt_at`, last reason and error; a row exists only while a unit is leased or failing. A leased row has an owner and expiry; a waiting or quarantined row has a clock. Migration 34 seeds rows for failing pending units; see [Work queue](#work-queue) |
| `acquisition_plan`                            | `plan_key`                                  | `year:<yyyy>` or `page:<watch_id>` rows the plan loop writes and acquire reads ([D-0218](../../journal/decisions/0218-the-loops-judge-the-year-gate-on-facts-and-acquire-from-a-plan-table.md)): `reason` (`eligible` or the gate's stop reason), `rank`, source, URLs, `detail_json`, `planned_at`, `run_id`. A pass cut by its deadline keeps older page rows; a whole pass deletes them                                                              |
| `guard_reasons`                               | `subject`, `reason`                         | why a fetch is held: subject `host:`, `watch:` or `loop:`, the loop that saw it, `since`, `last_seen_at`, `expires_at`, `seen` ([guards](fetching.md#guards))                                                                                                                                                                                                                                                                                           |
| `loop_state`                                  | `loop`                                      | each loop's latest tick: run, start and finish, `failed`, `error`, `counters_json`, `last_progress_at`                                                                                                                                                                                                                                                                                                                                                  |
| `derivation_building`                         | `generation_id`                             | a generation whose rows are being stored in chunks after its live write: unit, fingerprint, witness, label digest and rows, `rows_stored`                                                                                                                                                                                                                                                                                                               |
| `derivation_reads`                            | `stage`, `unit_kind`, `unit_id`, `read_key` | the read keys of a link unit's current generation (`project/<kind>/<id>`, `content:<name>/<id>`, `pool:<event>`), replaced at completion or cutoff; indexed by `read_key`, so it is the reverse index from a key to the units that read it (migration 35, `state/read_index.py`). Not a dispatch proof input                                                                                                                                            |
| `source_event_proposals`                      | `source`, `source_ref`                      | the event a `project/mapping/<source>` unit proposes for a listing of its source: `event_id`, `match_method`, `match_confidence`, and `placeholder_json`, the event the listing would create when nothing matches; replaced per source, read per event (`state/proposals.py`)                                                                                                                                                                           |
| `release_partitions`                          | `partition`                                 | each release partition's latest state (`event/<id>[+<id>...]`, `dancers/<k>`, `year/<yyyy>`): `inputs_digest`, `state` `built` with `artifact_digest`, or `withheld` with a `reason` (CHECKed), `detail_json`, `built_at`, `run_id` (`state/release_store.py`)                                                                                                                                                                                          |
| `release_artifacts`                           | `artifact_digest`                           | a partition artifact directory under `partitions/`: its partition, `bytes`, `created_at`, `last_named_at`                                                                                                                                                                                                                                                                                                                                               |
| `release_manifests`                           | `manifest_digest`                           | a release manifest: a `release_manifest` object since schema 36 (before, a file under `partitions/manifests/`); `bytes`, `created_at`, the `candidate_id` built from it, the publication gate's `verdict_json`                                                                                                                                                                                                                                          |
| `accepted_inputs`                             | `consumer`, `input_name`                    | `digest`; the input value whose invalidation work has been committed, not a stage completion record                                                                                                                                                                                                                                                                                                                                                     |
| `findings`                                    | `finding_id`                                | `kind`, `subject_kind`, `subject_id`, `watch_id`, `snapshot_id`, `severity`, `summary`, `evidence_json`, `suggested_override`, `opened_at`, `run_id`, `closed_at`, `closed_by`                                                                                                                                                                                                                                                                          |
| `source_events`                               | `source`, `source_ref`                      | projection of index observations: `name_raw`, `start_date`, `end_date`, `location_raw`, `url`, plus provenance; joins to `source_event_map` for the `event_id`                                                                                                                                                                                                                                                                                          |
| `backup_uploads`                              | `path`                                      | `sha256`, `uploaded_at`; upload optimization only, verified against the selected archive commit on restore                                                                                                                                                                                                                                                                                                                                              |
| `blob_classes`                                | `class`                                     | one lifecycle policy per stored class: location, roots, collector (`none`, `orphans`, `mark_sweep`), minimum age, whether a backup set carries it; seeded by migration 36 ([objects and packs](#objects-and-packs))                                                                                                                                                                                                                                     |
| `objects`                                     | `class`, `digest`                           | one row per file under `objects/`: `raw_bytes`, `stored_bytes`, `stored_at`                                                                                                                                                                                                                                                                                                                                                                             |
| `derivation_evicted`                          | `generation_id`                             | a generation whose references and unshared payloads left the file for its pack ([output packs](#output-packs-and-residency))                                                                                                                                                                                                                                                                                                                            |
| `source_generation_packs`, `label_body_packs` | `generation_id`                             | the `parse_body` or `label_body` object holding a stubbed body (`pack_digest`, `packed_at`); never updated ([body packs](#body-packs))                                                                                                                                                                                                                                                                                                                  |
| `storage_applies`                             | `plan_digest`                               | one note per `storage apply` or `gc --collect --apply` (`kind`), written once and finished once with its receipt                                                                                                                                                                                                                                                                                                                                        |
| `backup_sets`                                 | `set_id`                                    | one row per backup set: its `backup.json` digest, database size and digest, parts, files, transport, `uploaded_at`, `drilled_at`, `drill_json` ([backup sets](#backup-sets)); never fenced                                                                                                                                                                                                                                                              |
| `backup_objects`                              | `transport`, `key`                          | the segment that holds a file (`path@sha256`) for a transport; never fenced                                                                                                                                                                                                                                                                                                                                                                             |

Canonical tables use the published columns in [data model](data-model.md#tables).
Their `snapshot_id` provenance names the winning observation's snapshot.
Snapshots use the archive table with a published subset. `review_queue` is computed by build
from findings and current state.
`changelog` is not stored in SQLite; it lives in the candidate and
baseline directories. See [architecture](architecture.md#findings-and-review)
and [publishing](publishing.md#candidate-and-baseline).

`watch_id` is `sha256(source|kind|method|url|form)[:16]`, so discovery
is idempotent by construction. `observation_id` is
`sha256(watch_id|snapshot_id|kind|seq)[:16]`.

## Registry verification (H1)

`registry_verifications` records each registry content check separately from
claim provenance: watch, check time, HTTP status, body digest, interpretation
snapshot, extractor and parser versions, found or not-found outcome, usable
flag, and stable reason. `last_checked_at` remains the attempt clock;
`registry_fetched_at` remains the winning claim's source time. Neither is the
usable verification clock.

Changed content waits for a successful parse. Identical content and a valid
304 reuse its accepted interpretation after checking the body and extract
artifacts. Missing or corrupt artifacts retain their required digest (the
extract digest is on the referenced snapshot) and `missing_artifact` or
`corrupt_artifact`; they do not renew freshness. HTTP and transport failures,
failed parsing, and obsolete interpreter recipes do not qualify. H15 captures
the runtime artifact independently of manual version labels.

Migration recovers only recorded successful snapshot checks tied to intact
content and accepted current interpretations. It retains their original
`fetched_at`; attempt timestamps and migration time never manufacture
history. Recovery is idempotent. Doctor reports oldest usable verification
age, stale watches, and watches with unknown verification.

## State directory

```text
state.sqlite
blobs/                         content-addressed source and manual bodies
extracts/                      content-addressed serialized extracts
inputs/<hash>/                 captured config, overrides, vocabularies, versions
candidates/<candidate_id>/      data/, README.md, _meta/, BUILT, PUBLISHING, PUBLISHED
partitions/<aa>/<digest>/      release partition artifacts and sidecars
partitions/manifests/<d>.json  a phase 3 release manifest; storage apply moves these into objects/
partitions/.tmp-<id>/          an artifact being written; a crash leaves one, the collector removes it
objects/<class>/<aa>/<digest>  derivation_output, parse_body and label_body packs (gzip) and
                               release_manifest objects (raw JSON); <digest> = sha256 of the raw bytes
objects/.writing-<uuid>        an object being written; the collector removes one a day old
tmp/                           SQLite temporary files of gc, storage and the collector (never /tmp)
tmp/assembly-<id>/             a streamed assembly's staging and DuckDB spill; the next build removes one a day old
baseline -> candidates/<candidate_id>
holds/<hold_id>.json           one retention hold: who, why, when, what it keeps
gc/plans/<name>.json           written plans: retention by digest, storage-<digest>, collect-<digest>; not backed up
gc/receipts/<name>.json        one receipt per apply, storage apply, collection and reclaim; not backed up
checkpoints/run_*/             a full checkpoint (the default backup until backupSegmented)
checkpoints/sets/<set_id>/     a segmented backup set: db/part-NNNN.zst, index.jsonl.zst, backup.json last
checkpoints/segments/<sha>.tar shared tar segments the sets name; never rewritten
operator-hold                  the operator's hold: timers skip; storage apply requires it
RESTORE_PENDING                present until restore verification succeeds
state.intent, state.lock       reader-writer lock pair (below); never state
heavy.intent, heavy.lock       keeps every build and derive's units apart while `[release] streamed_lock` is true (the default)
loop-<name>.lock               one per loop; a second instance of a loop fails
control.lock                   short admission and control mutex
runs/
venv/                          disposable; rebuilt from the lock
uv-cache/                      disposable
```

Publication markers and the baseline have one owner,
[publish](publishing.md#candidate-and-baseline). There is no SQLite row
claiming a candidate directory exists. Raw bodies, extracts, and input
bundles are written and made durable before the transaction referring
to them. A crash can leave an unreferenced artifact; it cannot leave a
committed reference to an incomplete file. An artifact nothing declares is
never removed: it is "unknown", and the plan reports it for someone to explain.
Three paths remove state. `gc --apply` removes candidate directories no
release or recent build claims, once they are older than
`retention.collect_older_than`, from a written plan under both locks
([retention](#retention)). `storage apply` (under `operator-hold` and both
locks) and maintain's bounded eviction and body packing remove references,
payloads and body columns only after their pack is written and verified
([objects and packs](#objects-and-packs)). The collector (`gc --collect
--apply`, `gc --collect-closures`, or maintain's sweeps) removes objects,
partition artifacts and dependency sets nothing names
([collector](#collector)).

### Locks

`state/rwlock.py` owns the protocol
([D-0215](../../journal/decisions/0215-loops-share-the-state-under-a-reader-writer-lock.md)).
Lock files are `flock` locks, never copied into a checkpoint and never removed
by a restore.

- **Exclusive** (the default `open_database` mode): every CLI
  mutation, backup, restore, `gc`, `accept-year`. Take `state.intent`, then
  `state.lock`, both exclusive, within the command's lock timeout (0 in timer
  mode: skip as `skipped-overlap`). Hold both for the handle's life. Only this
  mode migrates, and only it is the writer lock (`holds_writer_lock`).
- **Shared** (`mode="shared"`, a named loop): take `loop-<name>.lock`
  exclusive, `state.intent` shared, `state.lock` shared, then release the
  intent. Every step is non-blocking; a failure raises `DatabaseLockedError`
  with reason `loop running`, `writer pending` or `writer running`, and the
  loop skips its tick. A waiting exclusive holder owns the intent, so no new
  tick starts in front of it.
- **Migration handoff**: a shared open of an older schema raises reason
  `migration pending`. `open_for_loop` then tries one non-blocking exclusive
  open, which migrates, and reopens shared; a loop that loses that race skips.
- **Heavy pair**: release takes `heavy.intent` and `heavy.lock` exclusive
  (waiting up to 900 s) for a whole or closure build and for a correction;
  a streamed (partitioned) build takes neither. Derive takes them shared,
  non-blocking, and starts no unit in a tick that cannot (F10).

Order: `loop-<name>.lock`, `state.intent`, `state.lock`, `control.lock`, SQLite.
Every writable connection waits up to 70 s for SQLite's writer (busy timeout),
the publication boundary's included (`publish/safety._connection`). A
request or operation admission waits up to 90 s for `control.lock`, then
raises `ControlTimeout`. A parse, project or link unit takes no control lock
(D-0238). Either timeout skips the unit or request and counts nothing.

## Invalidation

`state.work.unfinished_units` defines unfinished work. Parse retains its
durable queue and admission contract. Project and link compare desired inputs
with their materialized generations; their queue rows are scheduling hints.
Deleting a hint cannot hide unfinished work. `accepted_inputs` identifies the
captured inputs available to consumers. Output revisions describe changed
data, never whether another stage ran.

At the start of a tick or command, read config, overrides, vocabularies, implementation versions,
and the exact runtime artifact into a validated immutable input bundle. The
bundle also carries `policy/retention.json`, the retention limits in force as
values, because an optional table with defaults cannot be read back from the
captured file alone; see [retention](#retention). Each consumer
uses those captured bytes throughout the run, including build. The
checkout stays the source of corrections; saved bundles provide exact
replay and backup. A later checkout edit is accepted on the next tick. Capture
failure leaves the previous bundle and queues untouched and fails the
run. Manual stage commands perform this same acceptance step.

One transaction accepts the bundle, enqueues the union of work affected
by its changed inputs, and updates `accepted_inputs`. A crash before commit does neither; a crash
after commit leaves the work to drain. Repeated invalidations coalesce
by work key. Existing work runs against the newest accepted bundle;
accepting another bundle enqueues its full affected set, including units
already processed under the previous bundle.

| Changed input                                                                                                   | Work enqueued in the same transaction                                                                                               |
| --------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------- |
| New snapshot                                                                                                    | Parse that snapshot                                                                                                                 |
| `recipe/env`: interpreter, installed distributions, pyproject's `[project]` table                               | Parse every retained snapshot; queue every project and link unit as a hint; identical output keeps its generation                   |
| A kind's extract or parse code scope, even with unchanged version labels                                        | As that kind's `EXTRACT_VERSION` or `PARSER_VERSION` change below                                                                   |
| A project, link or build code scope                                                                             | Queue that kind's project units, or every link unit, as hints; build none. Identical output keeps its generation                    |
| `EXTRACT_VERSION` for a page kind                                                                               | Parse every archived snapshot of that kind, re-extracting before parsing; invalidate the watch's cached extract fingerprint version |
| `PARSER_VERSION` for a page kind                                                                                | Parse every archived snapshot of that kind from its stored extract                                                                  |
| Successful parse changing current observations                                                                  | Project their old and new scopes; calendar or index changes enqueue every source's mapping unit                                     |
| Event aliases, source URL overrides, event-matching vocabulary                                                  | Every known source's mapping unit, and the mapping of each source the file names                                                    |
| Other projection vocabulary or `PROJECTOR_VERSION`                                                              | Every mapping unit and every current or previously materialized canonical scope                                                     |
| Event projection changes                                                                                        | Link that event, including removed subjects                                                                                         |
| Registry dancer or placement projection changes                                                                 | The links that read the dancer or its placements' events; the pool probe's events ([read sets](#link-read-sets-plan-f20))           |
| Weights, nicknames, identity overrides, `LINKER_VERSION`                                                        | Link every event with canonical subjects or existing link rows                                                                      |
| Findings, published snapshot fields, source events, source map, canonical rows, dancers, or link outputs change | Advance their output revisions; build reads these directly                                                                          |

The affected set can be empty: mapping work is needed only when source
events, proposals or map rows exist, or an override file names a source.
Accepting empty files in a fresh state records their digests without
inventing scope work. A caller that still queues the retired
`project/map/all` (parse-scope code that may not change, F12) gets every
source's mapping unit in its place (`work.enqueue`).

`state/work.py` owns this dependency table. Source adapters do not decide
which later stages run. A write, its revision bumps, and its downstream
work are committed together. Only actual output changes bump revisions.
Project-owned column changes advance `canonical` (or `dancers` for the
registry scope, including registry placements); link-owned columns and
link tables advance `links`. Link never advances a revision to relay an
unrelated projection change to build.

Fair offline service rotates stages and unit kinds; initial ties put
mapping work, then resolutions, before other project scopes. Each unit is
one transaction; an event re-derives through its dependency on its
resolution, not inside it. Enqueueing includes old scopes so deleting or moving
an observation also removes its former canonical rows.

Link drains one event at a time, applying assignment, link rows,
candidates, and linked columns in one transaction. It does not dirty
itself through its own writes. Every unit deletes its queue hint only
in the transaction committing its output. A project or link hint whose unit
is already current (an A->B->A deploy) is deleted by offline selection once
its read snapshot closes, unless it was queued again since
(`derivations.drop_current_hints`). It matches a hint by an unchanged
`enqueued_at`. That is safe only because selection and the delete run back
to back under the single writer, and because a hint only helps the
watchdog: `candidate_units` also walks every known unit, so a lost hint
loses no work. Keep no write between the two. Each delete bumps
`history_dispatch_fence` (the 0029 trigger), so a large drop, about 35,800
after an A->B->A deploy, voids any acquisition dispatch proof taken earlier
in that tick, as unit runs already do. Project and link commit their
materialized generation in that transaction too. If a unit cannot complete,
it remains unfinished and downstream stages do not consume partial work.
Parse's handled failures are defined in [parsing](parsing.md#version-changes-and-failures).

Release builds on its own timer and interval
([scheduling: loops](scheduling.md#loops)). A budget stop leaves unfinished
queues for the next tick. Pending
publication reconciliation and necessary correction-only builds have
priority before ordinary acquisition and derivation. Build inputs
and artifact completion are owned by [build](build.md#build-inputs). Its
derivation generation records the selected inputs and exact artifact pointer.

Acquisition runs on its own timer even with downstream work pending, and
derive rotates eligible offline work fairly. Actual host limits, controls,
and backlog high-water marks govern collection. A derive tick excludes attempted
input fingerprints, rather than whole units, so later parses can supply
new inputs to a shared projection without reopening unchanged retries.
See [scheduling](scheduling.md) for the initial allocations and objectives.

## Read recorder

A unit's dependencies say which inputs it reads. The read recorder checks that
claim while the unit runs (`state/read_sets.py`, [D-0223](../../journal/decisions/0223-link-units-depend-on-what-they-read.md)).
A link's dependencies are its recorded read keys
([link read sets](#link-read-sets-plan-f20)).

- `process_unit` and `link_event` run the unit body inside `recording`. It
  installs a SQLite authorizer that notes each canonical table the body reads
  (the `TABLES` keys, source events and the map, coverage, series, the
  identity and journal tables, findings, observations, snapshots, year
  acceptance). It never denies a read and restores any authorizer set through
  `read_sets.set_authorizer`. A nested recording shares the outer one.
- Reads by derivation infrastructure (capture, currentness, completion in
  `state/derivation*` and `project/materialization`) and reads inside
  triggers are left out: they are not the body's input.
- `RULES` maps each unit kind and table to what covers it: a dependency entry
  (`source_observations`, `selected_project_calendar`, ...), a link read key
  family (`project/dancer`, `content:binding_ref`, ...), `effect_only` (a
  side effect such as a confirmation watch, a pool probe, or the registry
  association the years own), `own_output` (rows the unit replaces),
  `nested_unit` (a unit the body materializes) or `pending:<package>` (a
  known gap; none today). A link table read with no rule widens its read
  set to `content:table/<name>`, a digest of the whole table (computed once
  per read snapshot), and logs `read_set_widened`.
- A read table with no rule is an undeclared read. The unit still commits
  (I1); the worker logs `read_undeclared` and the run summary counts it under
  `undeclared_reads` as `stage/kind:table`. A widened read set and a pool
  probe that failed and widened count under `read_set_signals`
  (`read_set_widened`, `pool_probe_widened`); so does a dancer a link used
  that its load did not read (`dancer_read_after_load`: the load reads the
  pool, each subject's previous default and the dancers its decisions name,
  so this is a gap, read after the load's snapshot). Each should stay 0, and
  the watchdog's `[read-set-widened]` fires on any in the last day.
- Armed mode, `SWINGSET_READS_ARMED=1`, is for tests only. It raises
  `UnrecordedRead` for an undeclared table, for a canonical read by `project/`
  or `link/` body code outside a recording, and for `record` outside one. It
  also adds tables SQLite's authorizer does not report: a table joined by
  `USING` or `NATURAL` whose other columns go unread. Outside armed mode that
  table can be missed (plan F25). `SWINGSET_READS_REPORT=<file>` writes one
  JSON line per unit.
- SQLite authorizes a statement when it is prepared. If derivation code
  prepared the same SQL text first in the same recording, the body's later
  read of it comes from the statement cache and is not seen.
- `tests/test_read_sets.py` runs the pipeline fixtures armed with zero
  undeclared reads, and checks statically that `project/` and `link/` open no
  connection or authorizer and are reached only through `process_unit`,
  `link_event`, `accept_year` or `derive_one`, apart from a reviewed list of
  gate queries and tools. The history review pack runs the inventory and each
  year through `process_unit`.

## Identity decision state

Schema migration 9 adds the accepted append-only identity journal, acceptance
records, durable source references and bindings, reviewed reference migrations,
current link resolutions, and append-only assertion history. Canonical subject
removal retains the source bindings and a superseded assertion. The journal
revision advances on accepted decisions or reference migrations and participates
in a link's captured token alongside the journal digest.

Journal acceptance belongs to the captured-input transaction. It rejects any
changed or missing accepted decision ID and enqueues all current events and all
historical bound event IDs. An obsolete linker cannot commit or delete its work
if this token changed while it computed. A checkpoint includes the accepted
captured bundle and referenced archive artifacts; it does not promote a merely
captured but unaccepted journal. See
[identity resolution](identity-linking.md#durable-decisions-and-resolution-h8h9)
for source-reference continuity and decision semantics.

## Acceptance cases

- A weights-only change enqueues every link scope. Kill after acceptance
  and before the first scope; restart completes the queue without a
  second edit. Kill after any scope; committed scopes remain complete.
- Changing the weights again during a partial run re-enqueues all
  affected scopes; no event is left under the superseded weights.
- A vocabulary-only correction changes a canonical field with all
  links and snapshots unchanged. Build becomes due from `canonical`.
- An extractor-only version bump re-extracts archived bodies even if
  every parser version is unchanged and all prior parses succeeded.
- An alias move deletes the old scope's rows and creates the new rows
  in one transaction, including after a crash. A removed event has no
  dangling links or candidates.
- A new registry dancer re-links previously unmatched entries without
  relying on an existing candidate relationship.
- A newly projected registry record re-enqueues linking for retained older
  events, so deferred first-point identities do not depend on an event still
  being inside its intensive 30-day refresh window.
- Sustained collection and existing offline backlog both receive service.
  Once downstream work settles, build runs before acquisition borrows
  unused offline time. Resume preserves host usage and cooldowns.

## Requirement inventory in shadow (H11)

`findings` holds review findings and acquisition requirements. Added columns
record the policy, desired evidence fingerprint, state, source, next action,
blocking reason, retry time, attempts, and status and progress times. States
are `ready`, `retry_wait`, `waiting_for_source`, `needs_implementation`,
`needs_review`, `unavailable`, `out_of_scope`, and `satisfied`. A successful
attempt alone never satisfies a requirement. Owner replacement remains the
postcondition check for review findings.

`finding_support` retains the accepted owner evidence for review findings.
`requirement_scan` stores a bounded keyset cursor across all retained
years. Each plan tick checks at most 100 scopes before ordinary work. A later
page or pass recreates a deleted review finding from its active support, and
a re-created requirement keeps the `opened_at` its support recorded. Removed
scopes retire explicitly; they are not counted as successful repairs. The
scan is the data-quality inventory that `review_queue` publishes.

Migration 37 dropped the requirement history: `requirement_transitions`,
`requirement_attempts`, `requirement_cohorts`, `requirement_cohort_members`,
and the two `findings` triggers that wrote transitions. `attempt_count`
stays 0 for new rows. H11 executes no repairs. H15 derivation reporting uses
fingerprint comparisons and separately counts registered scopes with no
materialized generation. Older schemas retain an explicitly labeled
queue-based report.

## Isolated work attempts (H12)

Migration 11 adds `work_generations` and `work_attempts`. Queue insertions and
invalidations advance a per-unit generation without changing admission's
existing timestamp token. Explicit retries have a separate generation and a
recorded reason and request time.

`state.attempts.begin_attempt` durably records the unit, queue generation,
retry generation, work token, input fingerprint, run, and start time before
execution. Outcomes are `running`, `succeeded`, `blocked`, `transient`,
`unavailable`, `interrupted`, and `superseded`. Completion retains its time,
stable reason, evidence, and retry deadline when applicable. Its
`requirement_id` column is NULL from schema 37.

Success commits with the unit's output and queue completion. If the queue
changed during execution, the caller rolls back stale output and records
supersession. Failure keeps the pending unit; it cannot consume newer work.
The fingerprint identifies retry inputs: relevant source bytes, source/watch
identity, admission policy, and captured implementation inputs. Queue times,
attempt counts, and attempt findings do not change it. These fingerprints
control retries; H15 owns desired/materialized derivation generations.

Blocked and unavailable work does not retry the same inputs merely because
another tick or inventory scan runs. Changed relevant inputs or an explicit
`request_retry` releases that latch; the work queue's waiting clock makes that
request when it comes due. Transient and interrupted outcomes have a future
deadline, which the work queue sets. After taking the exclusive lock, the CLI
converts abandoned running attempts to interrupted outcomes and
releases every lease; it does not infer success. A running attempt whose queue
lease is dead is eligible, and the next lease closes it (see below).

A failed work unit writes no finding and no `finding_support` row
([D-0237](../../journal/decisions/0237-unit-failures-leave-the-review-queue-and-requirement-history-goes.md)).
Its `work_attempts` row holds the outcome, reason, evidence and retry time;
its `work_queue` row holds the reason, error, clock and quarantine. Migration
37 closed the open `work_attempt` findings as `out_of_scope` and set their
support inactive. Restoring an artifact does not finish the unit; its output
still has to commit. Doctor counts durable running attempts. This records
attempt state, not operating-system liveness.

## Work queue

Migration 34 adds `work_queue`, an overlay on `pending_work` and derivation
currency ([D-0216](../../journal/decisions/0216-the-work-queue-leases-backs-off-and-quarantines.md)).
A row exists only while a unit is leased or failing; success deletes it in
the output transaction. `state/queue.py` owns the policy.

- **Lease.** `derive_one` leases its unit in a short claim transaction that
  commits before its admission, as
  `<loop>:<run_id>` for 30 minutes (`cli` for a command). A
  live lease of another owner skips the unit (`lease_busy`). A lease is dead
  when it expired, when its owner is this process's own loop, or when this
  process holds the exclusive lock. Taking over a dead lease finishes its
  running attempt `interrupted` (`process_interrupted`) in the same
  transaction; the unit then waits on the failure
  clock below instead of running (`process_interrupted` outcome, nothing
  run). The `parse`, `project` and `link` commands call
  `release_all` after their recovery; a loop tick closes its own dead runs'
  attempts the same way and releases their leases.
- **Process death.** An attempt whose run died (a kill, an OOM at a
  `MemoryMax`, a stop timeout) is a `process_interrupted` failure, so a unit
  that kills its process quarantines at the third. A dead run's lease with
  no running attempt died in the admission and counts the same
  (`queue.died_unclaimed`). Its row has no input fingerprint, so a unit
  quarantined by three such deaths waits out the seven days even when its
  inputs change. One whose run finished on its own (its `runs`
  row or record file) was left by a fenced or busy write:
  `publication_fenced`, uncounted. So is one whose run wrote
  `runs/<run_id>.stop` (SIGTERM, then a kill at the stop timeout). A tick
  writes it only for a SIGTERM within its budget or under `operator-hold`,
  so a unit killed by its start timeout counts.
- **Escaped exception.** An exception that escapes `derive_one` leaves its
  attempt running; the offline loop closes it as a counted
  `unit_exception:<type>` failure (`queue.escaped`), unless that write is
  fenced or busy.
- **Admission.** One `BEGIN IMMEDIATE` checks the unit's pauses
  (`matching_pauses(for_unit)`), captures its inputs and begins its attempt.
  It writes no `execution_admissions` row (D-0238): the lease and the running
  attempt are the unit's in-flight record. A pause commits with
  `BEGIN IMMEDIATE` too, so it either lands first and holds the unit
  (`operator_pause`, nothing written, the lease given back) or lands after
  and sees the lease as draining.
- **Admission failure.** An exception in the admission (capture, attempt)
  rolls it back and records a `unit_exception:*` failure on the
  row alone, with no attempt. Selection honours such a row
  (`queue.holds`): a waiting, quarantined or not-yet-due row holds a unit
  whose latest attempt is absent, succeeded or superseded while its inputs
  are the failed ones. A fence or busy error in the admission or an operator
  pause writes nothing and gives the claimed lease back.
- **Orphans.** The maintain loop deletes rows whose unit has nothing left to
  do: no `pending_work` row, no running attempt, not leased, and for project
  and link units current.
- **Failure class.** The attempt's reason, plus `:<ExceptionType>` for
  `unit_exception`. Failures count while the class and input fingerprint stay
  the same; either change restarts at 1. The row keeps the last error
  message (500 characters); a blocked parse puts its extract or parse error
  there and in the attempt's `evidence_json`.

| Class                                                                                                           | Row                      | Clock                          | Quarantine        |
| --------------------------------------------------------------------------------------------------------------- | ------------------------ | ------------------------------ | ----------------- |
| `unit_exception:*`, `write_deadline_exceeded`, `nondeterministic_output`, `inputs_changed_spurious`, and others | `ready`                  | 15 min × 2^(n−1), at most 6 h  | at 3, for 7 days  |
| `unit_io_error`                                                                                                 | `ready`                  | 60 s                           | at 20, for 7 days |
| `parse_failed`, `admission_*` (blocked parses), `artifact_unavailable`                                          | `waiting_input`          | 24 h × 2^(n−1), at most 7 days | never             |
| `unsupported_page_kind`                                                                                         | `waiting_input`          | 30 days                        | never             |
| `process_interrupted` (the unit's process died)                                                                 | `ready`                  | 15 min × 2^(n−1), at most 6 h  | at 3, for 7 days  |
| `publication_fenced`, `write_lock_timeout`                                                                      | `ready`, count unchanged | 60 s                           | never             |

A transient attempt's `retry_at` is the row's clock, so `attempts.eligible`
and the queue agree; a new input fingerprint makes a quarantined unit
eligible at once. `queue.wake` turns due `waiting_input` and `quarantined`
rows `ready` and calls `request_retry` for a blocked or unavailable unit; derive
calls it once per tick. A retry of a waiting unit that ends the same
way is a `waiting_repeats`, not a failure. So is a waiting failure with the
same class and message as the row's last one after its input fingerprint
changed; its clock still restarts at 24 h. With no earlier message it is
fresh ([D-0249](../../journal/decisions/0249-a-waiting-failure-with-the-same-message-is-a-repeat.md)). A real supersession
(`inputs_changed`) and a fence or busy error (in the admission or while
processing) change nothing: under a full
fence derive writes nothing, and the next tick's recovery closes an attempt
left open uncounted. Under a narrow fence the queue is writable, so the tick
closes it the same way at once ([publication fences](#publication-fences)).
`swingset queue` reports rows; `swingset queue requeue` sets matching
quarantined or waiting rows `ready` with no count and requests a retry.

## Event completion persistence (H14 extension)

Accepted design; local enumeration and inventory implementation is covered by
offline tests. Deployment and complete extension acceptance remain pending. [Scheduling](scheduling.md#event-completion)
owns eligibility and rotation; this section owns their durable support.

Reuse `scheduler_parent_links`, watches, admitted source generations, request
accounting, work attempts, and release manifests. Persist an enumeration's
source reference, supporting parent snapshot and interpretation IDs, membership
digest, distinct request members, pagination-completeness label, and predecessor.
Membership uses source request identity, not canonical event IDs. A changed
alias cannot reset discovery age or discard pending requests. Retain admitted
removals and additions as transitions; reconstruct missing work hints from this
support. A parent pointer alone is not a complete enumeration witness.

Per host, class, and source event, retain turn position, issued turn usage,
first-known discovery, service and successful-progress times, blocker changes,
and the captured policy identity. Tie turn usage to `scheduler_requests` in the
same transaction as the host debit, including requests that fail. A crash after
issuance cannot refund usage. Shared pages have one debit owner and can support
several event enumerations. Derived counters are rebuildable views, never the
authority for successful completion.

Progress times advance with verified stage-output changes. Failure, an unchanged
poll, queue recreation, or a policy reload cannot reset them. Record enough
blocker transitions to compute eligible time; leave unsupported legacy history
unknown. Selection receipts identify the event, enumeration, policy, turn, and
reason. Aggregate unchanged blockers per tick to avoid an unbounded record per
skipped page. Backups and restores preserve these records with the evidence.

Bootstrap enumeration membership from retained admitted parents without source
requests. Recover discovery and progress times only from supporting records.
Migration creates pending work or explicit uncertainty, never successful fetches,
new host capacity, or inferred historical scheduling decisions. Published counts
come from the selected release, as defined by [coverage](data-model.md#event-completion-coverage).

Schema 22 adds replaceable `event_accounting_support_observations` for bounded
parent checks and immutable, change-only `event_accounting_receipts` for observed
assessment changes. They share progress policy and invalidation fences. They
record neither a completion authority nor historical timestamps before recording
began. Checkpoints retain them; normal restore activation invalidates fresh hints
without deleting the historical receipts. These tables do not authorize retirement. See [reporting semantics](operations.md#event-completion-reporting-h14-extension).

Schema 23 adds `event_retirement_observations` for one current enumeration edge
and immutable `event_retirement_receipts` for verified source-owned page
withdrawals. The proof digest excludes observation time and observer policy;
repeated checks cannot duplicate the same withdrawal. Current observations share
progress fences and expiry, while backups preserve historical receipts. A
withdrawal receipt neither deletes data nor establishes whole-event retirement.

Schema 24 adds replaceable `event_gap_observations` for explicit unavailable-origin
responses and `event_gap_revisions` for source-wide snapshot-domain invalidation.
Gap records retain exact support metadata and a digest, share progress policy,
enumeration, epoch and TTL, and also require the captured source revision.
They are separate from acquired/interpreted stage operations and successful
progress receipts. Snapshot changes and watch source moves/deletion invalidate
the affected source domains; rare support metadata rewrites and watch source changes also invalidate
positive stage observations through the ordinary global epoch. Ordinary new
snapshots preserve missing-to-success qualification. Backups retain these tables;
normal restore activation invalidates their freshness through the existing epoch.

Schema 25 extends gap observations with evidence-backed critical-unknown
dispositions. Generation changes and source-unit watch changes invalidate the
source gap revision, including generations never admitted. Observer policy v5
invalidates prior sampled classifications. Legacy records do not acquire invented
unsupported history. Known-page accounting combines verified interpretation,
unavailable origin response, or explicit unsupported proof, with separate counts.
This creates no successful stage operation, progress receipt, retirement authority
or publication acknowledgment.

Schema 26 retains bounded acquisition timing episodes, closed counters, per-run
summaries and the observation cursor. See [acquisition timing](event-timing.md)
for interval coverage, invalidation and unknown history. Timing is diagnostic;
it cannot authorize requests or establish event completion.

Schema 27 adds immutable `source_event_retirement_receipts` and
`source_event_retirement_proofs`. One withdrawn enumeration records one semantic
retirement; later verification can record a different proof without inventing
another transition. Current reports require fresh domain and admission fences.
Restore preserves receipts while invalidating current proof freshness. See the
[retirement contract](data-model.md#recorded-source-event-history-and-retirement).

Schema 28 retains per-host request spacing reservations and immutable reviewed
legacy-baseline receipts. The original effective gap and debit timestamp survive
crash and restore. A restored reservation cannot establish current in-flight
ownership or elapsed monotonic time; the ordinary gate imposes a fresh wait.
Legacy paid hosts keep an unknown gap until the explicit operating baseline is
recorded. See [fetch spacing](fetching.md#politeness-rules).

### Local enumeration and verification interfaces

Migration 16 adds `source_event_inventory` current pointers, immutable
`source_event_enumerations`, `source_event_enumeration_members`, indexed
`source_event_member_watches`, and `event_enumeration_inputs` bootstrap receipts.
None stores an authoritative acquired, interpreted, or complete flag.

`event_enumerations.bootstrap(database, now=..., limit=100)` processes at most
100 accepted decisions and 100 legacy watches. Parent evidence, membership,
transitions, and cursors commit together. Failed parent validation records an
explicit error without partial membership. Non-event inputs advance the cursor
with an ignored reason. Registry and calendar inputs are skipped without decoding
their large payloads. Source event membership follows actual parser purpose,
including EEPro autoindex and WDR awards JSON watches. Unknown legacy event or
round parsers remain explicitly unassessed; known discovery parsers cannot
become event groups merely through a mislabeled watch kind. Bootstrap makes no requests or watch scheduling changes.

`memberships(conn, watch_ids)` returns current source/reference/enumeration/request
associations through an index. It does not certify evidence or request eligibility;
blocked and revoked obligations remain visible.

`event_inventory.inventory(conn, archive, source=..., source_ref=..., now=...)`
uses the caller's read snapshot and an Archive without recovery. It returns
listed, acquired, interpreted, and observed unavailable distinct-page counts; supporting generations;
member blockers; predecessor and membership changes; verification time and basis;
and explicit pagination uncertainty. It hashes the actual local body and extract
files sequentially. Subsequent calls recheck bytes, so file loss reopens stages.
`known_pages_accounted_for` requires nonempty known membership and usable parent
and page evidence; it is not whole-event completeness or publication. No current
adapter proves whole-event pagination. Published count, eligible-service age,
and historical successful-progress time remain unknown in this local view.

Legacy-only events have unknown denominators. Older schemas return
`event_inventory_schema_unavailable` without migration. Canonical mappings are
reporting metadata and never change membership identity or discovery age.
[D-0009](../../journal/decisions/0009-retained-source-event-enumerations.md)
records the implementation choice and its limits.

## Derivation generations (H15)

Migration 14 adds a scope catalog, immutable derivation generations, retained
output rows, and interned ordered dependency sets. Migration registers existing
work without declaring any old output materialized. A desired-fingerprint
column is a diagnostic cache; changing or deleting it cannot establish that
output is current. Retired scopes remain discoverable so their old rows can be
removed.

SQLite change tokens advance in the same transaction as relevant inputs.
Completion records a signature of those tokens and the selected recipe. An
unchanged signature permits reuse of the already checked exact manifest; changed
tokens require the full comparison. Tokens cannot create a materialized pointer.
A `watch` token, and the `source_selections` token that source_index and
inventory certificates read, move when a watch's snapshots change or when a
source unit's `watch_id`, `unit_key`, `accepted_generation_id` or
`legacy_snapshot_id` changes; a write of `desired_fingerprint` alone moves
neither (schema 33).
Migration 33 also adds the `derivation_recipes`,
`derivation_equivalences` and `source_equivalences` tables for output cutoff.
A generation is materialized for a fingerprint when it was written for that
fingerprint or an equivalence row names the scope, the fingerprint and that
generation (`materialized_for`; `source_materialized_for` for parse). Reuse in
`desired()`, the slow path of `current()`, and the calendar replay guard
(`current_for`) all ask this; an equivalence for another generation of the
scope does not count. Build still also checks its artifacts.
Each prerequisite kind has a `replayed` token, keyed `[stage, kind]`. A
unit's dependencies list it as a direct `replay` entry per prerequisite kind,
never inside a `selected_*` set, and its certificate adds it wherever it reads a
kind's selected epoch or generation. `bump_replay` moves it, so dependents
re-run when a cutoff keeps a generation id but changes live rows. It also
queues a hint for every registered unit of each kind that reads the epoch
(`INSERT OR IGNORE`, so a queued unit keeps its time; a new one gets the
run's clock), since the watchdog sees only `pending_work`. Calendar,
dancer and source_index have no prerequisites and no replay entry. Adding the
entry changed every dependent fingerprint once. A link reads no per-kind
epoch: an event unit's replay also moves its own version (namespace
`replayed_unit`, key `[stage, kind, id]`), which only its link's event entry
includes, and queues that link.
Shared dependency manifests are memoized only within a query, with changes on
the same or another connection invalidating that memoization.

Each selection binds its scope, recipe, ordered dependencies, and context.
The input bundle keeps every package file, SQL schemas and package data
included, plus project lock inputs, interpreter identity and installed
dependency manifest digests. Installed RECORD hashes identify dependencies;
they do not audit every installed binary. Manual version labels remain
descriptive. Relevant override and policy inputs join each stage's recipe;
scheduler allocations do not.

A recipe names code by scope, not the whole package
([D-0207](../../journal/decisions/0207-scope-code-recipes-by-import-closure.md)).
`recipes/scopes.json` lists, by digest, the files in each scope's static import
closure over the captured bytes: every `import` anywhere in a module, parent
package `__init__` files, and non-Python files beside a reached module or in
its non-package subdirectories; never fixtures. `state/recipe_scope.py` holds
the entries:

| Scope                             | Entries                                                                                                                                                                                 |
| --------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `recipe/code/extract/<kind>`      | `sources.registry`, the owning adapter, `fetch.archive`                                                                                                                                 |
| `recipe/code/parse/<kind>`        | the extract entries' registry and adapter, `admission.contracts`, `.report`, `.generations`, `model.observations`; `schedule.parse` and `recipe_scope.PARSE_WRITERS` by their own bytes |
| `recipe/code/project/<unit_kind>` | `project.process`, `project.materialization`, the kind's projector                                                                                                                      |
| `recipe/code/link`                | `link.service`, `link.persistence`                                                                                                                                                      |
| `recipe/code/build`               | `build.service`, `build.generations`                                                                                                                                                    |

`recipe/env` digests `environment.json` and `project.json`, pyproject's
`[project]` table; the whole `pyproject.toml` and `uv.lock` are kept in the
bundle but a `[tool.*]` edit or a dev-dependency bump changes no recipe. A
project unit's recipe is its kind's project scope, `recipe/env`,
`policy/history_start` and its overrides; link and build likewise. A parse
recipe is `recipe/env`, `policy/history_start` and the kind's two scopes.
Every package path is in a scope or in `EXCLUDED`, with a reason; a test
enforces this. A bundle captured before scopes still yields the package-wide
`recipe/runtime`; after the first scoped bundle that input stays at the
absent digest.

Known cost: the storage, projection, link and build modules still import
each other, so each project and link scope holds 172 files and the build
scope 178. The scheduler, the loops and acquisition (`schedule/release_steps.py`,
`loops/`, `fetch/client.py`, `history/backfill.py`, `schedule/fairness.py`
and the like) are in no scope: an edit there re-runs nothing. `versions()` and
`repository_identity()` live in `versions.py` for that reason. An edit inside a shared module re-runs every project and
link unit; identical output keeps its generation (below), so this costs CPU,
not generations. Parse scopes hold 39 to 47 files, and a non-parser change
re-queues no parse. A shallow entry adds its own bytes and its packages'
`__init__`, not its imports or data files; tests pin which of its imports
fall outside its scope. A parse _retry_ still keys on `version/repository`,
so any deploy re-opens blocked parse attempts without re-parsing others.

The projection order is calendar, source index, and dancer scopes; history
inventory; one mapping per source; one resolution per event; event scopes;
then one unit per year for the association and coverage.
Inventory includes occurrence and listing enrichment. Previous inventory output
supports stable IDs but does not trigger its own next generation. An alias move
re-derives the source's mapping, the old and new events' resolutions, then
those events.
A link depends on what it read: its event, the dancers it used, content
digests and its pool version ([link read sets](#link-read-sets-plan-f20)).
Link-owned columns are excluded from its project dependencies.

Migration 32 stores each distinct output row once. The bytes live in
`derivation_payloads`, keyed by the sha256 of the canonical row text, and
`derivation_row_refs` records which generation uses which row, in what order. A
view named `derivation_rows` joins them, so every reader keeps the old five
columns. A recomputation that changes ten rows of a thousand therefore costs ten
rows. One function, `retain_output`, writes output, inside the caller's
transaction. It reads its rows in one pass and never holds a generation in
memory. What it does depends on residency: nothing when every row of the
generation is there, store the missing payload bytes when the references are
there without them, and store both when the generation is new. A new generation
has no label yet, so the caller completing it hands `retain_output` a function
that writes the label; the digest and row count come from the stream as it was
stored, and the recorded label is read back and compared. The path that puts
missing bytes back reads the whole generation through the view and checks that.
Either way the
generation ends up matching its `output_digest` and `row_count`; that the stored
rows are exactly the stream is what `PRIMARY KEY(generation_id,ordinal)` and
`UNIQUE(generation_id,table_name,record_key)` guarantee. It never moves a
pointer and never touches scheduling state, so restoring archived output later
is checked exactly as a fresh computation is.

References live as long as their label: in the file, or, once the generation
is evicted, as the lines of its pack
([D-0229](../../journal/decisions/0229-derivation-output-and-bodies-live-in-packs.md)).
Before schema 36, payload bytes were the one derivation record that could be
removed, and only by a transaction that carries its own row in
`derivation_payload_removal_authority`. That row cannot survive its transaction:
its deferred foreign key points at an always-empty table, so a commit that still
holds it fails. A grant therefore cannot be left switched on for later
processes. If one is ever written with foreign keys off, `PRAGMA
foreign_key_check` reports it, which checkpoint verification and recovery
already run, doctor lists it, and opening the database clears it. Nothing in the
pipeline writes the row today.

Schema 36 adds a second gate of the same kind, `storage_eviction_authority`,
under which old output leaves the file for packs and old bodies are stubbed;
see [objects and packs](#objects-and-packs).

A reference names its payload by digest with no foreign key, so bytes can be
archived while the reference stays; a generation whose bytes are not local
reads as no rows at all through the `derivation_rows` view, and every reader of
that view catches it with the row-count and digest check it already does rather
than using a partial answer. The scope pointer is not such a reader:
`current()` answers from the pointer and its signature, so keeping every current
generation's bytes local is the removal plan's job, not the view's.

Migration 32 fills the new tables, recomputes every generation's output digest
through the view, and drops the old table only when every one matches;
otherwise it raises and the whole migration rolls back with the old table
intact. It needs about twice the old table's bytes free while it runs. The drop
returns pages to SQLite's free list and does not shrink the file on disk.

Interning was the last migration on purpose, so schemas 30 and 31 are
deployable without it
([D-0167](../../journal/decisions/0167-intern-derivation-payloads-in-the-last-migration.md));
migration 33 follows it now that production is interned
([D-0206](../../journal/decisions/0206-add-migration-33-after-interning.md)).
At those schemas `derivation_rows` is still the one table: `retain_output`
writes it directly, no payload bytes can be missing, no payload can be removed,
and the plan says which of the two shapes it measured under
`payloads_interned`. Reading through the `derivation_rows` name needs no branch
either way, because it is a table before the split and a view after it.

Completion rechecks the selected inputs and prior materialized pointer. It reads
the output rows once unless it considers a cutoff (below). A new generation streams them through `retain_output`,
which stores them and records the generation from what it stored; a repeat of an
existing generation only fingerprints them and is rejected when the digest or row
count differs; it reads and writes no stored row, except that an evicted
generation is paged in from its pack before the pointer moves
([output packs](#output-packs-and-residency)). Completion then rechecks the inputs before moving the pointer.
Output, owned output-row history, revision bumps, and the new pointer commit
together.
A late worker cannot overwrite a newer generation. A crash before commit leaves
the scope unfinished; a crash after commit preserves complete output. Immutable
generations retain exact dependency references even after newer inputs arrive.
Equal inputs with different output are rejected. H12 attempt controls still
govern retries; they cannot make unfinished derivations disappear.

A project or link re-run whose output equals its selected generation's keeps
that id: an early cutoff
([D-0205](../../journal/decisions/0205-cut-off-identical-derivation-output.md)).
`complete()` takes its rows as a callable or a sequence, which it may read
twice; `materializing()` passes a callable, and a bare iterator never cuts
off. It considers a cutoff only when the pointer is the one the selection saw,
no sibling in its consistency group captured its provisional id (such a member
is pinned). Output alone decides: a kept generation may name a superseded
upstream one, since releases take current pointers and the closure path
withholds such an anchor
([D-0224](../../journal/decisions/0224-releases-from-partition-artifacts.md),
which dropped D-0205's closure-stable rule). It then reads the
rows once without writing, fences the inputs again, and compares the digest and
row count with the kept label. On a match it stores the dependency sets, a
`derivation_recipes` row and a `derivation_equivalences` row, and updates only
`desired_fingerprint`; the pointer and the `selected` epoch stay. The group,
`selected_generation()` and the certificate use the kept id. If `canonical`,
`dancers`, `links`, `source_event_map`, `source_events` or `observations`
moved between capture and completion, the replay rewrote live rows, and the
kind's replay epoch moves once the group has certified. Otherwise it stores the
rows from a second read, which must match the first. Build never cuts off.
`events.coverage_tier` belongs to the year (history's before plan F17), so no
other scope's output records it. A calendar, map, event or dancer generation from before phase 1 did, so the
comparison takes that one column out of the kept rows first
(`materialization.moved_columns`,
[D-0212](../../journal/decisions/0212-cut-off-across-the-moved-column-and-held-parses.md));
the build applies the year's value last either way. A re-run of a generation it reuses, by fingerprint or equivalence, moves
the replay epoch the same way, and a calendar or inventory reuse replays
the continuity support recorded with that equivalence. Output that differs
for identical inputs raises `NondeterministicOutputError`, a failed attempt
with a retry clock. `[derivation] cutoff = false` in `config/sources.toml`
turns cutoff off for parse and derivation alike (`swingset/cutoff.py`, set per
unit by `derive_one`, by `run_intake` and by `accept-year`; the offline
evidence and recording imports and `review_pack` take no config and keep it
on); recorded equivalences stay readable.

Large units complete in stages, so no write holds the whole output (phase 2,
WP4). A completion stages when `derive_one` runs it (`derivations.chunking`),
no other unit's group is open, its own group completes nothing else, and it is
a project year or its kept generation has more than `CHUNK_ROWS` (5,000)
rows. The `accept-year` command stages its year the same way: its
acceptance commits with the year's live rows (A), then B and C run as below;
if they stop, the derive loop resumes the building generation. Grouped
completions such as a `source_event` with its event, and callers already in
a transaction, keep the single path.

- **A**, the processing transaction: live rows as before, the same checks as
  `complete()`, and a `derivation_building` row (generation id, fingerprint,
  the first capture's witness, the end-of-A witness, and a sticky `moved`
  flag). A resumed A keeps the first witness and never clears `moved`, and
  another generation's building row of the unit sets it: a retry's own
  capture already sees what a killed A moved. The pointer, attempt, queue
  row and `pending_work` row stay. A commits.
- **Pass 1** reads the output from `Database.reader()`, a read-only second
  connection pinned to one snapshot, and labels it. A label that differs from
  the building label, or from the recorded label of a generation it reuses,
  raises `NondeterministicOutputError`. Output equal to the kept generation's
  (the cutoff rule above) goes to C.
- **B** stores a new generation in write transactions of `CHUNK_ROWS` rows
  from a fresh snapshot. The first writes the generation row with the pass-1
  label and the building label. An ordinal already stored must match its
  table, key and payload digest; the rest are inserted. `rows_stored`
  reports progress only; the stored references decide what B skips. The stream's digest must equal the label.
- **C**, one short transaction: the inputs fence, a pointer check, then the
  equivalence or the pointer flip (a new generation must have every row
  referenced), the replay epoch when a cutoff or reuse follows an A that
  moved live rows (the building row's `moved`, or any other building row of
  the unit; `complete()` applies the same rule),
  dependency checks, the certificate, the building row's delete,
  `finish_attempt(succeeded)`, `queue.succeed` and the
  `pending_work` delete.

Between A and C live rows lead the pointer. A unit with any building row is
not current, so no dependent runs and the unit itself runs again, even when
its inputs went back to the pointer's. A failure after A finishes the attempt
transient and keeps the building row. A retry with the same inputs has the
same generation id: A reruns, B verifies the stored ordinals and continues. A
building row whose generation row is gone starts over. A partial generation
whose building row was collected is still building: when the unit's inputs
return to it, A gives the new building row its recorded label and B finishes
it. C counts the references of
a reused generation too, so the pointer never selects a partial one. An input change makes
a new id, and C of the old run raises `SupersededWorkError`. The unit's next
successful run deletes the building rows of other ids
(`derivations.collect_orphans`, I6). Generations and references are
immutable, so a partial generation stays; `unfinished_excluded` keeps it,
and any generation still building, out of the release closure's generation
lists and the retention window, and the retention plan lists it
`incomplete`. A single-path completion that meets a partial generation of
its own id, with or without a building row, finishes storing it inline. `cycle_progress` skips ids in
`derivation_building`. The maintain loop counts building rows, and the
watchdog's `[building-orphans]` names any not advanced for 24 hours.

`recipe_inputs` fails closed: once `recipe/env` is accepted, a project, link
or build unit whose code scope is not accepted raises `RecipeScopeMissing`.
Offline selection then skips that kind only, names it in the derive record's
`readiness_errors` and fails the tick; the other kinds keep running.
`pending_units` counts such a unit as pending, so readers that ask whether
work remains fail closed instead of raising out of the tick.

Equivalences, their recipes and their root dependency sets are immutable, not
permanent (D-0206). Nothing collects them yet; invalidation step 3 will, by
this rule: a derivation equivalence is live while its fingerprint is the
scope's `desired_fingerprint` or it is the newest naming the scope's pointer;
a source equivalence while its fingerprint is the unit's; a recipe while a
live equivalence names it. `gc --plan` reports their counts, live counts,
recipe bytes and the root sets only equivalences name, under `equivalences`.
A code-only deploy adds about 35,800 derivation equivalences and 33,000 root
sets.

Snapshot extract reuse additionally requires the kind's extract scope digest
(`recipe/runtime` for a bundle from before scopes).
An unchanged body and manual extractor version cannot reuse an extract from a
different artifact. A failed extraction never relabels a previous extract.

Doctor reports unfinished project and link scopes from this comparison, raw
parse work, registered and unmaterialized scope counts, and retained generations.
The last durable build materialization is separate from publication progress.
H16 owns release closure and publication coverage; H15 keeps the existing normal
build barrier until that revision lands.

### Year units

`project/history/all` is retired for `project/year/<yyyy>` (plan F17,
[D-0222](../../journal/decisions/0222-derive-history-per-year-and-the-event-map-per-source-and-event.md)).
`derivation_dependencies.RETIRED` names it: its scope row and generations
stay, but no catalog yields it, `refresh()` never registers it,
`process_unit` drops its hint without running, and migration 35 deleted its
queue rows.

- **Which years.** Every `events.year`, every reporting year of an event
  (`substr(event_month,1,4)`), the reporting year of every placement already
  associated with an event, every year with `coverage` rows, and every year
  already registered or queued. A placement year with no event reported in
  it has nothing to associate and gets no unit.
- **What a year writes** (`project/history.finalize_year`). The association
  of registry placements reported in the year, at or after
  `history_start`, by series and month as before; the `sheets_partial` tier
  of its events; and its `coverage` rows (`project/coverage.rebuild_year_coverage`),
  each keeping `last_changed_at` while unchanged. Coverage counts a year's
  events by `events.year`, with every placement associated with them
  whatever month it reports: a December event reported in January counts in
  December's year, while January's year associates its placements. A
  changed association moves the `canonical` revision. Output: the year's
  coverage rows, its events' `coverage_tier` and the `event_id` of every
  placement reported in it.
- **What it reads.** Prerequisites: every calendar, the inventory, and the
  events of the year or reported in its months with their resolutions. Content groups: `year_placements` (placements reported in the year
  without `event_id`, which it writes, plus placements reported in another
  year that name one of its events, with `event_id`), `year_acceptance`,
  `year_findings` (open `history_year` and `phase1_year` findings for the
  year) and `snapshot_transport` (the transport of those rows' snapshots).
  Replay epochs: calendar, dancer, inventory, resolution and event. A unit's content
  never includes a column it writes, so its own run never makes it stale
  (F23): a dancer's reconcile that moves `event_id` reaches the year through
  the dancer epoch. Its certificate names the other-year associations
  directly, since no token moves with `event_id`.
- **Readiness.** A year waits while any calendar, source index or inventory
  scope is unmaterialized, then for its prerequisites. A prerequisite that is
  quarantined, waits for input or backs off, or that the pacing gate holds
  (the inventory inside its interval), is settled at its pointer
  (`derivations.settled_reason`), so a year does not wait out the
  inventory's 12 hours after a dancer change, and one failing event does not
  freeze its year; each settled prerequisite is named in the tick's
  `settled_prerequisites`. No link waits for a year: it reads the
  association as content, and a year that moves it queues the link
  ([read sets](#link-read-sets-plan-f20)).
- **Hints.** `process_unit` queues hints for the years a unit touches, before
  and after it writes: an event's years, a source event's events' years, a
  dancer's reporting years that have an event and the years of the events its
  placements name, a resolution's event's years, and every year for a
  calendar or the inventory.
  An edit to `overrides/series_aliases.csv` or `overrides/finding_reviews.csv`
  queues the inventory and every year. Currency decides what runs.
- **Staging.** A year always takes the staged path under `derive_one`, like
  history did. `accept_year` derives its year alone inside that year's
  capture and recording; the `accept-year` command stages it.

## Mapping per source, resolution per event (plan F19)

`project/map/all` is retired like `project/history/all` ([D-0222](../../journal/decisions/0222-derive-history-per-year-and-the-event-map-per-source-and-event.md)): its generations stay,
nothing schedules it, and a stale hint is dropped. Index scopes are pages, so
one listing can sit on several; the split is per source.

- **`project/mapping/<source>`** (`project/map.propose`). One per source in
  `source_events`, `source_event_proposals` or `source_event_map`, or queued
  when an override file names it. For each listing of the source it proposes
  the target the map chose: an override (`event_aliases.csv`, then
  `source_urls.csv`); else, as `alias`, the held occurrence the inventory's
  series-and-month rule (`project/history.match_listing`) names, when the
  inventory gave it this source and its dates overlap the listing's
  ([D-0253](../../journal/decisions/0253-a-results-listing-follows-its-series-occurrence.md));
  else the one catalog event (`wsdc_status` not `unknown`) with the same
  normalized name and overlapping dates; else, with a name and an end date, a
  generated id and the placeholder event the listing would create. The rule
  reads `series_aliases.csv` and the registry names the inventory wrote
  (`series` and held events), never `registry_placements`. Ambiguous and
  undated listings get no proposal. It replaces its source's
  `source_event_proposals` rows; they are its output. Prerequisites: every
  calendar, its source's index pages, the inventory; content group
  `source_listings` (its source's listings without clocks).
- **`project/resolution/<event_id>`** (`project/map.resolve`). One per event
  that is proposed, mapped, owns a resolution row, or owned a row of the
  retired `unmatched_source_events/all` or `override_events/all` scopes. It
  writes the event's `source_event_map` rows from its proposals (a listing
  another event held moves here; rows whose listing left are deleted) and the
  event row in scope `resolution/<event_id>`: for a generated id, the
  inventory's row when the inventory owns it (the registry-enriched
  collision), otherwise the listing's placeholder (the last in source and
  ref order); for an override, the row already there or the override's
  placeholder; for a catalog match, the row it already owned. A placeholder
  keeps the live `coverage_tier`, which its year owns. With none of these it
  deletes its row, and a row no scope owns any more goes, detaching the
  placements that named it (D-0191). Its first run releases its event from
  the retired scopes. Output: its map rows and its scope's event row.
  Prerequisites: every calendar and the inventory; content group
  `resolution_proposals` (the proposals naming the event). It also waits
  (`derivation_dependencies.waits_for`, not an input) for the mappings of the
  sources that propose the event or whose map rows name it, so a switch from
  the map never deletes a row before its source has proposed.
- **Events and source events.** `project/event/E` depends on
  `resolution/E` when that unit exists; its `source_mapping` group stays. A
  source event depends on the resolutions and events its listing maps to.
  Unmapped, it succeeds with no output and waits.
- **No global reconcile.** Neither unit re-projects events or calls
  `reconcile_registry_events`; calendars and dancers still do, and the years
  own the association (F17, F23).
- **Gate.** A pending mapping or resolution closes the projection-pending
  gate, as the map did, unless the paced inventory holds it.

## Link read sets (plan F20)

A link used to depend on every dancer, so one registry page re-queued all
2,934 links. Now `link/event/E` depends on what its run read ([D-0223](../../journal/decisions/0223-link-units-depend-on-what-they-read.md)). The run records
its read keys; completion and cutoff replace its `derivation_reads` rows with
them, and its fingerprint and certificate are formed from them
(`derivation_dependencies.link_inputs`). A link that never ran reads the
default set: every key below but the dancers and source references.

| Key                          | Value                                                                                    | Covers                                                                |
| ---------------------------- | ---------------------------------------------------------------------------------------- | --------------------------------------------------------------------- |
| `project/event/E`            | the event unit's selected generation and its own replay version                          | contests, rounds, entries, judges, placements of E                    |
| `project/dancer/<id>`        | the dancer unit's selected generation (a row digest if no unit)                          | the dancer row and registry placements of each dancer it used         |
| `content:event_row/E`        | digest of E's events row, without clocks or `coverage_tier`                              | the row a calendar, the inventory or a resolution owns                |
| `content:registry_event/E`   | digest of the placements with `event_id=E`, without clocks                               | confirmations and points; the association the years and dancers write |
| `content:missing_identity/E` | E's open prelim `missing_identity` findings                                              | points field sizes                                                    |
| `content:journal`            | journal digest, `identity_decisions` revision, reference migrations                      | decisions and migrations                                              |
| `content:binding_ref/<ref>`  | the ref's bindings to other events' subjects, with their existence and resolution reason | continuity checks (`DecisionResolver.binding_problem`)                |
| `pool:E`                     | `derivation_input_versions(namespace='link_pool', key=E)`                                | every other dancer: candidate pools, name rarity, judge eligibility   |

The event's source observations stay a group (`source_observations`); the
release closure follows them. The `content:binding_ref` entries, about one per
subject, form one `binding_refs` group, stored once while they are unchanged.

- **Dancers it used.** Every candidate, every dancer whose registry row at E
  a confirmation query returned, every printed id, and the ids it links. A
  printed id with no dancer yet is read too, so a dancer who appears later
  re-queues it.
- **Capture and commit.** The load records each key's value; which dancers
  it used is known after resolving, so the load keeps every dancer unit's
  generation and the row digest of every pool dancer no unit materialized.
  The evidence and these values come from one read transaction
  (`derivation_query.read_snapshot`), so a write that lands during the load
  is seen by both or neither: a value taken after the evidence could certify
  output computed from older rows. `commit_resolution` passes the read set with those values to
  `materializing`, which selects the generation from it inside the write and
  raises `SupersededWorkError` when any value moved (`derivations.check_reads`).
  The admission's selection came from the previous read set, so only its
  recipe must match.
- **Readiness.** A link waits for its event, its resolution and the dancers
  its read set names that have a unit: a scope, a queued unit or an
  observation (`prerequisites`). A printed id the registry lacks has none and
  is no prerequisite, or the link would never be ready again (F27: 13 links
  on the production copy); its key reads the row's absence, and the dancer's
  first pointer queues the link. No year: the association is content
  (`content:registry_event`). A failing or paced prerequisite is settled at
  its pointer ([year units](#year-units)). One that never ran also waits
  while any dancer scope is unmaterialized and not settled. Nothing lists or
  checks the whole registry, so listing ready links no longer takes 26 to 29
  minutes (phase 1 known limit; the worker time is unmeasured).
- **Hints.** A project scope whose pointer moves to a new generation queues
  every unit whose read set names it (`read_hints`, the reverse index); an
  event replay queues its link. `process_unit` also queues the links whose
  registry placements or event row a dancer, year, calendar, inventory or
  resolution changed (`hint_state`). A dancer no longer queues every link.
  Hints only help the watchdog; currency decides.
- **Pool probe** (`link/pool.py`). A dancer unit compares each of its
  dancers' signature before and after its write: normalized name, surname
  word, surname initial, merge and judge eligibility. On a change, or when a
  dancer appears or disappears, it bumps `pool:E` and queues the link of
  each event where `generate_candidates` pairs the old or new name with an
  entry or judge of the same surname initial (judges only when eligible;
  only judges for an eligibility change alone), and of each event whose live
  candidates share the old or new surname word (name rarity). It runs only
  once some link has run. A probe that raises bumps every event with a
  subject of either initial and logs `pool_probe_widened`.
- **Journal.** Any journal change re-queues every link (over-approximate).
- **Known limit.** A link's resolution state reads its own previous default
  id: a revocation records `revoked` once and `unresolved` on the next run.
  A re-run with unchanged inputs after a revocation therefore differs and
  raises `NondeterministicOutputError`. Scheduling never re-runs a current
  link, so this needs a forced run; the soundness test compares answers,
  not that state.

## Partition artifacts

Stage 1 of a partitioned release writes one artifact per partition
([build: partition artifacts](build.md#partition-artifacts)). The rows are
the lifecycle; the files are content-addressed and never rewritten:

- `release_partitions`: one row per current partition, `built` with its
  artifact or `withheld` with a reason. `detail_json` holds the partition's
  generation digest (`content`), the snapshot and parser pairs its rows name,
  the event lists of its event generations (immutable, so the next build
  groups events without reading rows), counts, whether any support was
  legacy, the pairs read from live observations, the listings it holds, and
  the withheld detail. A
  build writes a row only when the inputs digest changed, and deletes the
  rows of partitions that no longer exist.
- `release_artifacts`: one row per artifact directory, with `last_named_at`
  set by every build that names it.

A build writes rows 200 partitions to a transaction. It checks each artifact
directory inside that write, so an artifact the collector removed after the
build found it gets no row and is written again next time.

**Collector.** The maintain loop runs the [collector](#collector)'s two
classes daily (`collector.collect_artifacts`), 500 to a write: artifact
directories and `release_artifacts` rows that no `release_partitions` row and
no manifest of a retained candidate (the baseline, pending candidates, the
newest three built and the last three published) names, once `last_named_at`
is before the cutoff; directories with no row and `.tmp-` directories likewise;
release manifests (the object, its `objects` and `release_manifests` rows and
any phase 3 file) that no retained candidate and no current build generation's
candidate names, written before the cutoff (`manifests`); and the scratch a
killed build left under `state/tmp` (`assembly-*`, `partitions-*`,
`closure-*`) after a day (`scratch`). A retained manifest that cannot be read
stops the artifact sweep: it removes nothing and says `unreadable`. A release
manifest is a raw `release_manifest` object since phase 4; `manifest_file`
finds the object first, then a phase 3 file, and `storage apply` moves those
files into objects. It is outside `gc --apply` because an artifact
holds no input, only a copy of generation rows the build can write again. A
build that finds an artifact collected while it ran builds stage 1 once more
at once instead of failing.

**Retention.** The sidecars of a retained partitioned candidate's manifest
name every generation their partitions read; those are roots
(`release_manifest`, [roots](#roots)), so `gc` never archives what a retained
release names.

## Objects and packs

Phase 4 of the [architecture plan](../plans/architecture.md) takes the bulk
nobody reads often out of `state.sqlite` and into files, gives each kind of
stored thing one policy, and collects what nothing names
([D-0229](../../journal/decisions/0229-derivation-output-and-bodies-live-in-packs.md),
[D-0231](../../journal/decisions/0231-collect-every-unnamed-dependency-set-and-unrooted-object.md)).
Every label stays in the database; what moves is the output and the old
bodies behind it. On the 2026-09-29 copy that took the file from 64.8 to
31.6 GB; the rest is mostly dependency sets other sets name
([phase 4, W1](../plans/architecture.md#9-migration-plan)).

### Classes

`blob_classes` has one row per class, seeded by migration 36: where it
lives, what keeps it, its collector and whether a backup set carries it.

| Class                             | Stored as                                                        | Collector                                                                 |
| --------------------------------- | ---------------------------------------------------------------- | ------------------------------------------------------------------------- |
| `body`, `extract`, `input_bundle` | `blobs/sha256/`, `extracts/`, `inputs/`, written by their owners | none: permanent; an undeclared file stays unknown (D-0143)                |
| `derivation_output`               | a pack: one generation output, gzip                              | [collector](#collector), weekly                                           |
| `parse_body`, `label_body`        | a pack: one old parse body or build recipe, gzip                 | collector, weekly                                                         |
| `release_manifest`                | a release manifest, raw JSON                                     | collector, daily ([partition artifacts](#partition-artifacts)) and weekly |
| `partition_artifact`              | a directory under `partitions/`, indexed by `release_artifacts`  | collector, daily                                                          |
| `dependency_set`                  | rows in `derivation_dependency_sets`; not in a set's files       | collector, weekly                                                         |

`state/objects.py` stores the files of the four object classes under
`objects/<class>/<aa>/<digest>`, where the digest is the sha256 of the raw
bytes. A write streams into `objects/.writing-<uuid>`, fsyncs it, renames it
into place and fsyncs the directories; an intact object already there is
kept, and a damaged one is replaced. The caller then writes the `objects`
row (raw and stored bytes, `stored_at`) in the transaction that refers to
the object, so a crash leaves only an unindexed file, which the collector
removes after a day. A present row keeps its values but its `stored_at`
moves up: a writer that reused the file has just written it. `record`
checks the file after it writes the row and raises `ObjectUnavailable` if a
collector removed it meanwhile, so no row names a file that is gone. `open`, `lines` and `verify` raise `ObjectUnavailable`
for a missing, damaged or wrong-digest object. `store_for(conn)` finds the
state directory from the connection, so the same code works on a checkpoint
or a restore scratch.

Schema 36 adds `storage_eviction_authority`, a gate of the same kind as the
payload removal authority ([derivation generations](#derivation-generations-h15)). Under it a
transaction may delete row references, dependency sets, payloads and
`derivation_evicted` markers; set a source generation's `manifest_json`,
`result_json` and `report_json` together to the stub `{"packed":true}` (once a
`source_generation_packs` row names its pack) or back; and do the same for a
build label's `recipe_json` with `label_body_packs`. Every other column stays
immutable, and a pack row cannot go while its row is a stub. Opening the
database clears a stale row of either gate.

### Output packs and residency

A generation's output pack is a `derivation_output` object whose bytes are its
label stream, one `canonical((table, key, payload))` line per row in ordinal
order, so its digest is the label's `output_digest` and one pack serves every
label with that output (`state/output_packs.py`). A generation is _resident_
when every reference and payload is in the file and _evicted_ when a
`derivation_evicted` row says its references left for the pack; complete means
either, so an evicted generation is never partial or `incomplete`. Readers of
output go through `output_packs.rows` (label checked, `GenerationUnavailable`
on a mismatch or missing pack), `row` or `table_rows`; only the modules a
static test allows name `derivation_rows` or `derivation_row_refs` in SQL.
Current pointers and building generations stay resident, so the partitioned
build's two current-pointer joins keep their SQL. A pointer flip to an evicted
generation pages it in first: inline completion in its transaction, a staged
run in step B at most `CHUNK_ROWS` references per write (step C refuses a
larger one still evicted). An evicted generation with its pack on disk is
local for holds and `require_local`; the retention plan marks it `evicted`
and, once anything is evicted or packed, reports resident, evicted and partial
counts and pack bytes under `residence`.

### Eviction

`swingset storage plan` and `storage apply <digest>` move cold output into
packs once (`state/storage.py`,
[D-0229](../../journal/decisions/0229-derivation-output-and-bodies-live-in-packs.md)).
Hot means a current pointer or a building generation; a generation whose
references are short or miss a payload stays for the collector; every other
complete generation is evictable. The plan streams the sorted evictable
`(generation_id, output_digest)` list, the kept list and the evicted list into
sha256 digests through temporary tables under `<state>/tmp`, so the same state
gives the same digest in bounded memory; it is written to
`gc/plans/storage-<digest>.json` with an estimate outside the digest. Apply
needs `operator-hold`, the writer lock and then the control lock. It
recomputes the plan and stops if the digest moved, commits a `storage_applies`
note, writes one pack per distinct output (kept if already there), verifies
each pack's digest and line count, and then, in one exclusive transaction,
copies the kept references aside, drops and recreates `derivation_row_refs`
from its saved SQL, puts the kept rows back, recreates its indexes and
triggers in their old order, bumps `history_dispatch_fence` once, marks the
rest evicted and deletes the payloads nothing names under the storage
authority. Before it commits it checks the index and trigger SQL against the
saved copy, that every kept resident generation is still resident, and
`foreign_key_check` on both tables, and it finishes the note with the receipt.
A crash before that commit leaves only packs and the open note; rerunning the
digest resumes. Rerunning a finished digest returns its receipt, also written
to `gc/receipts/storage-<digest>.json`. `storage.evict` is the maintain
loop's version: cold, complete generations at least a day old, packed and
verified first, references deleted from the highest ordinal down, at most
`CHUNK_ROWS` per transaction, so a stopped generation keeps a prefix that the
next tick or a page-in continues. It waits while a publication is active.
One generation that cannot be packed, read or verified is isolated: counted
in `failed`, its id in `failures`, its rows charged to the budget, the guard
`loop:maintain evict_failed` (24-hour clock), and the pass goes on. It then
waits out a retry window, 1 day doubling to 7 (`gc/evict-retry.json`, a
hint that is never backed up), counted in `deferred`; the guard stays while
any generation waits. An `OSError` of a full disk or quota or an unwritable
store stops the pass and starts no window. A damaged pack of a resident
generation is written again from the file (`pack(repair=True)`, which
apply's verification uses too). Each pass first puts back every `objects`
file whose row stands and whose file is gone from the newest local backup
set that holds it (`restore_lost`; `restored`, `lost`). A pack of an evicted
generation that is missing or damaged also comes back when a unit reuses
that generation: the unit's own output, checked against the label, is
written back and the pack written again (`output_packs.rebuild`; inline
completion in its transaction, a staged run in step B a chunk per write). A
unit with an evicted generation over `CHUNK_ROWS` is staged, so such a
page-in never runs whole inside one inline transaction. Apply, eviction, body packing and the
collector run with `secure_delete` off (`disk.fast_delete`): the worker's
SQLite zeroes freed pages through the WAL otherwise, which made the
rehearsal's move write a 30 GB WAL (W2). Apply refuses before it writes
anything unless the disk, quota included, has twice the kept references'
bytes plus 10 GB, and truncates the WAL after its move.

### Body packs

Old parse bodies and large build recipes are packed too
(`state/body_packs.py`). A source generation's `manifest_json`, `report_json`
and `result_json` become one `parse_body` object, `canonical({"manifest",
"report", "result"})` of the three strings, so equal bodies share one object; a
`source_generation_packs` row names it and the three columns hold the stub.
`recipe_json` stays, since parse code reads it for every row. A build label's
`recipe_json` over 1 MB becomes a `label_body` object named by
`label_body_packs`. `storage plan` lists both as stages: the targets are every
unprotected row, packed or not, so a resumed apply keeps its digest. Apply
writes every object, verifies every one, then stubs source rows 1,000 to a
transaction and build labels one at a time, each stub rechecking that the row
still hashes to its object, before the output move. `storage.pack_bodies` is
the maintain pass: bounded rows, each rechecked against the rules in the
transaction that stubs it. `storage restore-body <generation_id>` writes one
body back after checking the object against the pack row, and deletes the pack
row.

A source generation is protected, never packed, when it is: in any state but
`accepted` or `superseded`; a unit's accepted generation; a unit's newest by
`(created_at, generation_id)`; without an admission decision; on the
predecessor chain from an accepted generation back to the previous accepted
one; named by `source_equivalences`, an open finding, or an admission
review's cohort; read by the kept `event_*` subsystem (`event`: every event
enumeration's own generation, the keys of its `parent_support_json` and the
`generation_id`s in its members' `support_json`, which event coverage and
event retirement decode again); named anywhere in a retained closure-mode
candidate's closure proof (`storage.retained` reads every `generation_id`,
`event_coverage` included); or created within 7 days of the start of the
current UTC day. The maintain pass rechecks `event` against a snapshot of the
two append-only enumeration tables plus the rows added since. A build label is protected
while a pointer or building row names it, while a retained candidate's
artifact row is in its output, or for 7 days. Every reader of the three
columns:

| Reader                                                                   | Rows it reads                                 | With a stub                                                                      |
| ------------------------------------------------------------------------ | --------------------------------------------- | -------------------------------------------------------------------------------- |
| `admission/generations.py` `_cutoff` (parse scope)                       | accepted, undecided, newest: protected        | the stub is not the new triple: no cutoff, a new generation (tested)             |
| `admission/select.py` `revoke_generation` (parse scope; no caller today) | the chain to the previous accepted: protected | `KeyError`, rolled back; `body_packs.check_revocation` names it (tested)         |
| `admission/select.py` `admit_generation` (parse scope)                   | the staged row: protected                     | not reached                                                                      |
| `admission/support.py` `generation_failure_codes` (parse scope)          | the generation just admitted: protected       | `KeyError` (tested)                                                              |
| `history/origin_dispatch.py` (parse scope), `platform.py`, `backfill.py` | the accepted generation: protected            | not reached (tested: no accepted row is a target)                                |
| `history/captures.py`                                                    | the report only of a held state: protected    | not reached                                                                      |
| `admission/policy.py` review and activation                              | a review cohort: protected                    | `KeyError`, or "Reviewed corpus changed" (tested)                                |
| `admission/page_evidence.py`, `schedule/event_*`, `admission/event_*`    | enumerations, parents, claims: protected      | `decode_generation` raises: the fingerprint and id no longer match (tested)      |
| `build/event_coverage.py` `_Reader.parent`                               | enumeration parents: protected                | not reached (tested: an old parent's witness validates after packing)            |
| `state/parse_recovery.py`                                                | a unit's newest 8 for one snapshot            | the stub manifest is not a list: `Unassessed`                                    |
| `build/closure_support.py`                                               | accepted generations a closure names          | `body_packs.resolve` (tested on a closure-mode baseline)                         |
| `build/partition_checks.py`                                              | every generation of a snapshot and parser     | report and observations from the pack, one read per pack a build (tested)        |
| `build/closure_validation.py` proof fingerprint                          | a closure's sources                           | hashes the stub and the pack row's digest (tested)                               |
| `state/retention.py` backup closure                                      | every manifest                                | `body_packs.pack_manifest` once per pack; an unreadable pack is missing (tested) |
| `state/closure_collection.py` (build recipes)                            | every build label                             | `body_packs.recipe` (tested)                                                     |

### Collector

`state/collector.py` is one mark-sweep over every stored class
([D-0231](../../journal/decisions/0231-collect-every-unnamed-dependency-set-and-unrooted-object.md),
extending D-0200). The cutoff is the start of the current UTC day minus one
day; only what is older goes, so a plan and its apply on one day agree.

| Class                | Kept when                                                                                                                                   |
| -------------------- | ------------------------------------------------------------------------------------------------------------------------------------------- |
| `derivation_output`  | a label's `output_digest` names it                                                                                                          |
| `parse_body`         | a `source_generation_packs` row names it                                                                                                    |
| `label_body`         | a `label_body_packs` row names it                                                                                                           |
| `release_manifest`   | a retained candidate, a current build pointer or one of the last 3 published releases names it                                              |
| `partition_artifact` | a `release_partitions` row or a kept manifest names it ([partition artifacts](#partition-artifacts))                                        |
| `dependency_set`     | a generation, an equivalence, a scope's proof, a recipe, any candidate directory or a kept set names it, or it was written after the cutoff |

A recipe names a set by its continuity support or its release closure (public
and private; a packed build recipe is read from its pack). A kept set names
others by `"fingerprint"` in its JSON array, followed to a fixpoint; a false
match keeps more. A set written after the cutoff is kept with everything it
names: a staged run's step A captures sets that only step B names, and B
writes all of them again in the transaction that names them, so even a set
collected between A and B comes back (tested). Orphans: `objects/.writing-*`,
`objects/.collecting-*` and files under `objects/` with no row, both after the cutoff by mtime, and
the references of a partial generation, old, not evicted, that no pointer,
equivalence or building row names (D-0217): deleted from the top,
`CHUNK_ROWS` to a write, with payloads nothing else names; the label stays
partial. Bodies, extracts and input bundles have no collector: an undeclared
file stays unknown (D-0143).

The mark runs in one read transaction and keeps roots and victims in
temporary tables under `<state>/tmp`, never in Python; a set's created_at is
read only for sets nothing names, since it lies past a value of up to hundreds
of megabytes. Deletes go 500 to a write, each rechecking its rule there. An
object's row goes and its file is renamed to `objects/.collecting-<class>-<digest>`
in the same write, renamed back if it rolls back and removed after the commit,
so a writer reusing the file never records a row for a file that is gone, and
a crash leaves only a `.collecting-` file the orphan sweep removes. A set delete first adds the roots of generations,
equivalences, sets and candidate manifests committed since the mark; the set
with the highest rowid at the mark is never deleted, so every later insert has
a higher rowid and is seen. Deletes run under the storage authority; before
schema 36 they lift `derivation_dependency_no_delete` inside the write, as
D-0200 did, and need `operator-hold`. A publication in progress stops the
sweep. `gc --collect --plan|--apply` runs it once ([operations](operations.md));
maintain runs `sweep` with a stop callback. On a synthetic state of 200,000
labels, 100,000 objects and 300,000 sets the whole sweep peaks at 107 MB,
1.23 times a run ten times smaller (`test_collector_memory.py`). On the
phase 4 rehearsal clone, after `storage apply`, the plan took 54 s and the
apply 170 s at about 200 MB, and removed 598,088 sets, 0.10 GB: the large
sets are all named through another set.

## Backup sets

The backup contract: a set restores the whole state offline. Its database
parts and the segments its index names hold everything a restore needs,
evicted output and packed bodies included, since every `objects/` file is
in the closure. Fetching them from the archive is the only network a remote
set needs; a local set needs none. Activation still checks the public hub,
as every restore does; the install and the drill do not.

`backup --segmented` writes a set (`backup/segments.py`;
[D-0232](../../journal/decisions/0232-segmented-backups-with-a-weekly-restore-drill.md)).
It opens as the loop `backup`, under the shared state lock, so the loops keep
running; the drill holds the same loop lock, so the two never overlap. It first
removes scratch sets and segment writes over a day old (`clean_abandoned`,
which maintain also runs daily), and refuses unless the disk, quota included,
has the database's bytes in use (page count less the free list) plus 20 GB.
It runs `VACUUM INTO` (one read transaction), then, under the
control lock, reads the file closure from that copy: `artifact_closure` (bodies,
extracts, input bundles), every `objects` row, legacy release manifests and
the partition artifacts a current partition or retained manifest names, the
baseline and pending candidates, `holds/` and the other loose files a
checkpoint copied. Files that change in place are copied into the set's
scratch under the same lock. The copy is taken outside the control lock, as a
checkpoint's is, since every database open and control waits on that lock; a
newer hold names only files already on disk.

| File                 | Content                                                                                                   |
| -------------------- | --------------------------------------------------------------------------------------------------------- |
| `db/part-NNNN.zst`   | the copy as one zstd stream, cut every 4 GiB; sha256 of the raw file and of each part                     |
| `index.jsonl.zst`    | `[class, path, sha256, bytes, segment]` per file, by path; sha256 of the gunzipped bytes for gzip classes |
| `segments/<sha>.tar` | PAX tar, mtime 0, state-relative names, at most 4 GiB, named by its own sha256                            |
| `backup.json`        | canonical; names the parts, the index, every segment the index needs, and `missing`; written last         |

A file goes into a new segment when its `path@sha256` key has no
`backup_objects` row for the transport, or its row names a segment the
transport no longer holds. Each new file is checked against its digest as it
is read. The transport (`backup/transport.py`) sends new segments, the parts,
the index, then `backup.json`; only then are `backup_objects` and
`backup_sets` written, so a failed upload records nothing, removes the
unrecorded local set, and the next run resends. `--local` uses `checkpoints/`
itself, `--destination` a directory, and the default the private dataset
`skeswa/swingset-backup-sets` (never the checkpoint archive): one commit per
set, the newest 3 remote sets kept. After each upload the transport's
`collect` removes the segments no kept set names and, on the Hub, squashes
the history so their bytes go, refusing while the repository holds a path
no set wrote; `checkpoints/remote.json`
records the set, the time and the archive's files and bytes, and
`backup_objects` rows of removed segments go
([D-0234](../../journal/decisions/0234-the-backup-archive-collects-unnamed-segments-and-squashes-its-history.md)).
No file exceeds 4 GiB. Local pruning keeps the newest set and the newest
drilled one, and the segments they name; scratch younger than a day stays.

An `objects` row whose file is gone is put back first from the newest local
set that holds it (`SegmentRecovery.recover_object`, checked against its
name; `restored`); failing that, when the newest local index names it and
the transport still holds that segment, the new index names the same segment
(`absent`). A file the closure names that is still missing (such an object,
a retained manifest or named artifact that is gone, or a pack row whose
object has no `objects` row), or a new file that does not match its digest
or decompress, no longer fails the set: `backup.json` lists it under
`missing` as `[class, key, reason]` and `[backup-incomplete]` goes to
`ALERTS.log`. The tar stays whole: a bad read is padded to its declared
size and left out of the index. A missing body, extract or input bundle
still fails the set. Install, and so the drill, refuses a set whose
`missing` is not empty, and checks that every `objects` row has its file and
every pack row an `objects` row. The collector leaves manifests and
artifacts alone while the backup holds `loop-backup.lock`, since a set lists
files after its `VACUUM INTO`.

`restore_set.install` fetches every file and checks every digest before it
decompresses, joins the parts (raw sha256), checks the schema,
`integrity_check` and `foreign_key_check`, extracts each indexed member with
its digest, and checks the restored closure, all in a scratch inside the
target (the object check runs on a quick install too). Only then does it move
files into place and make the baseline link.
`restore --backup-set <id|latest> [--from <dir>]` wraps it in
`RESTORE_PENDING`, the public hub check and activation, as `restore
--checkpoint` does. `SegmentRecovery` restores a lost body or extract from a
local set's segment after `LocalCheckpointRecovery` (`backup/recovery.py`),
and a lost object for the backup, maintain's evict and `storage
recover-objects`; it reads each set's index, and each segment's tar headers
with every member's offset, once per instance into a lookup on disk;
loops and the CLI build that chain, parse scopes never do. The
full checkpoint stays the default backup until
`services.swingset.backupSegmented` is set. The weekly
[restore drill](operations.md#restore-drill) calls `install` alone into
scratch and writes `backup_sets.drilled_at` and `drill_json`. A
set of 50,000 files and a 218 MB database peaks at 173 MB of RSS
(`test_segments_memory.py`, bound 300 MB). A set taken while maintain's
collector removes one of its objects records it as missing, and the next set
is whole again.

## Accepted source generations (H7)

Migration 8 adds `source_units`, immutable `source_generations`, append-only
`admission_decisions`, immutable `admission_reviews`, and `admission_policies`.
Units hold desired fingerprints and accepted pointers. Generations retain the
ordered input manifest, exact recipe and captured input digests, observations,
coverage, field accounting, guard results, previous generation, and work token.
Only the decision state changes; evidence cannot be updated in place.

Existing V1 origin selections bootstrap as `legacy_unassessed`, with their
original snapshot pointer retained. They gain neither an accepted generation
nor removal authority. New archived calendar, newsletter, and directory
evidence does not become legacy merely because the migration is running.
Each approved phase-one archived index snapshot has a separate native admission
unit. Phase-two fallback captures share the watch unit and follow its explicitly
selected archive URL, rather than the greatest historical capture time. The
attempt retains that selected URL so a changed watch can veto stale completion.

Selection compares current inputs and guards in the same transaction as the
observation writer and downstream invalidation. The recipe's inputs, and that
comparison, cover only the page kind's parse inputs: its recipe and its two
version labels (`admission.generations.parse_inputs`, D-0207). An unrelated
accepted input does not supersede an in-flight parse. A source miss is dated absence
evidence, never deletion permission. Revocation records its explicit evidence,
withdraws only that generation's source claims, and cannot be reversed by
retrying the same input fingerprint.

Migration 33 adds immutable `source_equivalences(unit_key, input_fingerprint,
generation_id, recipe_json, work_token, created_at, run_id)`. A re-parse whose
result, report, contract and manifest equal the kept generation's writes one
row instead of a generation (D-0205; rules in
[parsing](parsing.md#source-admission-h6-and-h7)). The kept generation is the
accepted one, a shadow unit's undecided one, or an enforced unit's newest
generation held in `needs_review` or `waiting_for_inputs` when admission would
hold the parse the same way; the parse then reports that state with no new
decision (D-0212). A repeat of the kept
fingerprint writes nothing. `admission.select.source_materialized_for` treats a
row as a parse of that fingerprint, and admission's currency check uses it.
Updating only `desired_fingerprint` moves no derivation watch token. A revoked
generation also revokes the fingerprints its equivalences name
(`admission.generations.revoked_fingerprint`; the evidence readers use
`schedule.event_evidence.REVOKED_EVIDENCE`), so the same inputs cannot be
accepted again after a cutoff. The readers also refuse by snapshot and
parser label, and an equivalence shares its generation's recipe label, so
that query returns one row per revoked generation, plus any equivalence
whose label differs; re-parses do not spend the readers' row budgets.

`admission.support.interpretation_support` is the read-only publication check.
It distinguishes accepted support, explicit revocation, legacy unassessed
evidence, and unselected or superseded interpretations. Legacy is reported
separately and is never returned as automatically usable. `selection_digest`
pins policies, desired and accepted pointers, legacy classifications, and
revocations for publication's boundary checks. `admission_summary` reports
per-kind states and oldest staged creation times without decoding every report.

## Durable controls (H13)

`control_state` holds the monotonic control revision. `control_events` retains
pause, resume, expiry and legacy-import history. Schema 12 imports existing
pauses without inventing their original actor or creation time. The restricted
control connection writes only these tables and `operator_pauses`; it never
migrates, accepts an input bundle, or changes repair evidence.

`execution_admissions` records each action's checked revision, start time,
run/candidate references and active, uncertain or settled state.
`execution_dependencies` records its required sources and requirement kinds.
A request additionally records its host. The ledger holds actions with no
lease: `request`, `fetch`, `acquisition`, project-stage operations (kind
`project`, `event_*` and CDX ids), `registry_crosscheck`, `build` and the
publication kinds. A leased unit carries no admission (D-0238):
`admission()` refuses `parse` and `link`, `work_` ids and a work attempt, and
schema 37 deleted the 902,488 unit rows. The short control mutex serializes
pause servicing with new admission; output settlement joins the existing
transaction without reacquiring that mutex. The maintain loop deletes settled
rows older than 30 days, and their scope rows, in batches of 5,000
(`admissions_pruned`); publication rows, the newest `build` row and the
newest completed one (all `build_deferred_for_interval` and `release_lag`
read), and rows `scheduler_requests` or `history_origin_requests` names
stay. `scheduler_requests` has no collector,
so a request row lives as long as it does: on the 09-29 copy 14,546 of
14,570 request rows were named, and only fetch, acquisition and operation
rows (about 28,700) aged out. The ledger still grows by about 1,800 request
rows a day; maintain reports its size (`admissions_rows`), the watchdog's
`[admissions-growth]` fires past 600,000, and
[maintenance item 15](../plans/README.md#maintenance-queue) tracks a
collector. An acknowledged pause fences later
admissions while prior work drains. `controls.status()` lists as draining the
unsettled admissions and the leased `work_queue` units (`action_id`
`lease:<stage>/<kind>/<id>`, scope from `control_scopes.for_unit`, with
`lease_until`) while the lease is live; an expired lease is listed under
`dead_leases` and drains nothing. A lease whose scope cannot be read
counts against every pause and carries `scope_error`. A pause's receipt
reads the drain after its commit, so that read never holds the write lock
or undoes the pause. `pause --wait` waits for both. `recover_admissions` is the exclusive-open safety of migrate,
restore and the locked CLI commands, which hold the state exclusive, so every
active admission is dead (D-0238, D-0239).
Restore activation recovers abandoned admissions for every schema that has
`execution_admissions`, starting at schema 12. Local and request actions settle
as interrupted; publications remain uncertain until receipt reconciliation.
This recovery does not depend on the event-pressure tables added in schema 19.
When those tables exist, activation also advances their observation epoch in
the same transaction. The transaction commits before `RESTORE_PENDING` is
removed; schemas through 11 remain compatible and perform neither change.

Outer immediate worker transactions run under a 60-second wall-clock alarm,
the same for every unit; savepoints inherit it. Writes are small by
construction (I2), so the alarm is a safety net: a firing rolls the write back
and counts in `write_deadline.fired()` ([D-0217](../../journal/decisions/0217-large-generations-store-in-chunks-and-the-write-deadline-is-an-alarm.md)). The deadline starts
once `BEGIN IMMEDIATE` holds SQLite's writer: time spent waiting for another
process's write is not counted against it. Each process counts `writes`,
`write_wait_seconds_total` and `_max`, `write_seconds_max` (writer held, begin
to commit) and `write_lock_timeouts` (a busy timeout, raised as
`sqlite3.OperationalError`). A run resets them at its start, and its record
carries `write_deadline_fired`, `write_seconds_max`, `write_wait_seconds_max`
and `write_lock_timeouts` for that run (`write_deadline.run_counters`). Loop
records also carry `peak_rss_bytes`, the process's peak
resident memory, which the phase 2 memory gates read.
Interrupted output rolls back before the attempt records its transient outcome
and retry time (D-0198). Read snapshots remain available to a concurrently serviced
control and do not consume this write budget. A long unit cannot keep reacquiring
the write lock ahead of a waiting control.

Reports resolve the same action dependencies as admission. Pauses are overlays,
not requirement-state transitions or successful repair. Expired rows remain
visible to read-only reporting until a mutation records expiry. A selective
resume leaves overlapping controls and automatic host pauses intact. Unknown
selectors are rejected against registered sources, retained hosts and actual
requirement kinds. No retry history, accepted generation or identity decision is
cleared by a control change.

Control reporting reads `control_events` to union matching pause intervals once
per action scope. Resume closes a recorded interval; expiry caps it even before
a later mutation records the expiry event. Legacy-import events separate an
unknown earlier duration from the known interval after import. Reports keep
unknown overlapping durations unavailable and retain evidence wall ages. These
diagnostic clocks use current dependencies; they do not claim reconstructed
historical ownership or host-budget eligibility.

## Publication fences

An active publication admission fences writes with `BEFORE` triggers that
raise `active publication fences semantic writes`
(`state/publication_fence.py`). Every migration from schema 12 refreshes
them, so a new table is covered. The control tables above are never fenced,
nor are the backup records `backup_sets` and `backup_objects` (schema 36).

| Admission kind                      | Candidate               | Fences                                               | Loops                                              |
| ----------------------------------- | ----------------------- | ---------------------------------------------------- | -------------------------------------------------- |
| `publication` (legacy: `publish`)   | closure-mode or settled | every other table                                    | all skip (`publication_in_flight`)                 |
| `publication_narrow` (plan phase 3) | partitioned (D-0224)    | what the publication's check reads (`NARROW`, below) | acquire, plan, derive tick; maintain, release skip |

The narrow list comes from `publish/safety._validate_partitioned`, its
correction token and the input check. `tests/test_publication_fence.py` pins
it and runs the check under an authorizer to show it reads nothing else.

| Table                                                          | Rows fenced                                      |
| -------------------------------------------------------------- | ------------------------------------------------ |
| `meta`                                                         | `input_bundle_hash`, `identity_journal_digest`   |
| `revisions`                                                    | `identity_decisions`                             |
| `identity_decisions`, `identity_journal_acceptances`           | all                                              |
| `admission_policies`                                           | all                                              |
| `source_generations`                                           | `state='revoked'` (a revocation, or its removal) |
| `derivation_scopes`, `derivation_generations`                  | `stage='build'`                                  |
| `derivation_row_refs`                                          | rows of a build generation                       |
| `derivation_evicted`, `label_body_packs` (schema 36)           | rows of a build generation                       |
| `objects` (schema 36)                                          | release manifests, label bodies, build output    |
| `source_generation_packs` (schema 36)                          | rows of a revoked generation                     |
| `derivation_payloads`                                          | deletes only (payloads are shared by content)    |
| `release_partitions`, `release_artifacts`, `release_manifests` | all                                              |

An insert is fenced only when it adds a key, so `ON CONFLICT DO NOTHING` and
`INSERT OR IGNORE` on a present row pass (parse upserts its policy row;
derive re-accepts an unchanged bundle). An update of `meta` or `revisions` is
fenced only when the value changes. A database without every narrow trigger
admits a partitioned publication as `publication`. At most one publication
of either kind is active. A unit write that meets a narrow fence is closed
in its tick: attempt `interrupted`, `publication_fenced`, uncounted, retried
in 60 s ([work queue](#work-queue)). Recovery keeps either kind uncertain
until its receipt is reconciled.

## Retention

A saved computation is three things with three lifetimes. The **label** (the
`derivation_generations` row and its dependency set) is permanent; nothing here
ever deletes one. The **output** is its row references and the
`derivation_payloads` rows they name; it lives in the file or, once the
generation is evicted, in a pack kept as long as the label
([objects and packs](#objects-and-packs), D-0229). Only a dependency set
nothing names is collected ([collector](#collector), D-0231). The **current pointer** is `derivation_scopes.materialized_generation_id`,
owned by the pipeline; retention never moves one.

`state/retention.py` does one walk and produces two lists:

- **durable**: every generation that has a label. Its bytes must exist in at
  least one checked place forever. An ordinary superseded generation is on this
  list, so it is never "unknown".
- **local**: every generation the walk reaches from a root. These stay in the
  live database, so every backup carries them and a restore needs no network.

Durable minus local is archivable. `gc --plan` writes down what the lists say,
and `gc --apply` removes only files from it
([D-0147](../../journal/decisions/0147-only-a-planned-locked-apply-removes-anything.md)).
Output leaves the file another way: `storage apply` and maintain's eviction
move it into packs, which keep it durable and local, so an evicted generation
with its pack on disk counts as local for holds and `require_local`. The plan
marks it `evicted`.

### Roots

Roots are read from state that already exists, so no second list can drift:

| Root                | What it pins                                                                                                      |
| ------------------- | ----------------------------------------------------------------------------------------------------------------- |
| `baseline`          | The generations the baseline candidate's release closure names                                                    |
| `pending_candidate` | The same, for every pending candidate                                                                             |
| `release_manifest`  | For a `partitioned` baseline or pending candidate, every generation the sidecars of its manifest's artifacts name |
| `pointer`           | Every `derivation_scopes.materialized_generation_id`                                                              |
| `finding`           | Generations open findings declare in `finding_support_references`                                                 |
| `hold`              | Generations named by a file under `state/holds/`                                                                  |
| `restore_marker`    | The current pointers and pending candidates, while `RESTORE_PENDING` exists                                       |
| `operator_hold`     | The same, while the `operator-hold` file exists                                                                   |
| `window`            | The newest `retention.recent_window` generations of each scope                                                    |

The two markers pin the current pointers and nothing more; neither records which
computation it was about, and an operator who needs more writes a hold
([D-0138](../../journal/decisions/0138-recovery-markers-pin-current-pointers-and-nothing-more.md)).

From every root generation the walk follows the dependency set through the same
manifest reader the release closure uses, adds every generation it names, and
keeps going. It does not use the release selector, which refuses two generations
for one scope; the walk collects by generation id and allows many per scope, and
the one-per-scope rule stays where it is, in release validation. It does not
follow `previous_generation_id`. So a recent link generation keeps the older
project generation it was built from, even after that scope has moved on, and
the window only picks roots; it never replaces the walk. A generation with
equivalences also names their dependency sets, and the walk follows those too.
Each dependency set is decoded once per walk, however many generations share it.

Files are the same closure a checkpoint uses: everything snapshots, source
generations, hosts, open findings' declared references, input bundles, the
retained candidates and the holds reach. A hold names a digest without saying
which kind of file it is, so whichever of `blobs/` or `extracts/` has it is the
file it holds; `hold add` refuses a digest that is in neither.

A checkpoint computes that closure and copies the files under the control lock,
after the writer lock the command already owns and after the database copy, so a
hold cannot be committed for a file between the two and be carried without it. A
caller that already fenced a wider operation with that lock keeps its own fence;
the lock itself is not re-entrant
([D-0164](../../journal/decisions/0164-a-checkpoint-holds-the-control-lock-across-its-closure-and-copy.md)).

### Unknown

Five things have no owner: payload bytes no reference names, payload bytes only
a reference with no label names, a row reference whose label is missing, a file
under `blobs/`, `extracts/` or `inputs/` that nothing declares, and a file an
open finding declares that is not on disk. Doctor reports them and nothing
removes them until someone works out what they are
([D-0143](../../journal/decisions/0143-an-undeclared-file-is-unknown-in-the-plan.md)).
It reports both the count of each kind and the items behind it, the digests and
the paths, cut to the same limit as the rest of the report, so nobody has to
write a plan file out to find out which item to look at
([D-0157](../../journal/decisions/0157-an-operator-report-names-the-items-not-only-the-count.md)).

Unknown payload bytes are counted apart from both lists. They are never added to
the bytes archiving would give back, because nobody can say who owns them. A
file an open finding declares but which is not there is reported the same way
and is not an error: the closure keeps whatever is on disk, so one wrong
declaration cannot stop a backup
([D-0144](../../journal/decisions/0144-unowned-bytes-are-unknown-and-a-wrong-declaration-is-reported.md)).

Payload bytes are measured with SQLite's `octet_length`, not `length`. A payload
is stored as text, so `length` would count characters and under-report every
accented name by the bytes its accents cost. These are the numbers the size cap
is read against, so they are real bytes.

### Holds

`swingset hold add` writes one file per hold under `state/holds/`, with who
asked, why, when, and the generation ids and artifact digests it keeps. It runs
under the control lock, walks the closure first, and refuses, naming the ids to
restore, if any generation it reaches is not completely local: references
present, every payload present, and the count matching the label, or evicted
with its pack on disk. A named
artifact digest has to be a file under `blobs/` or `extracts/` already. Nothing
is written when it refuses. Naming a generation in a finding gets the same check in
`replace_findings`, in the caller's write transaction under the writer lock
rather than under the control lock; an apply holds both locks, so neither kind of
new root can appear while one runs. That check covers what the write newly
declares, not every declaration in the batch: a generation a finding already
declares is already a root, and rechecking it would let one archived generation
named by one unchanged finding refuse every other finding for that owner
([D-0162](../../journal/decisions/0162-the-residency-check-covers-only-newly-declared-generations.md)). Holds ride in checkpoints; written plans do not
([D-0140](../../journal/decisions/0140-a-hold-is-one-checked-file-under-the-control-lock.md)).

### Knobs

`config/sources.toml` may carry a `[retention]` table. An absent table or key
means the default, which lives in `RetentionConfig` in `src/swingset/config.py`.
Production sets `max_database_bytes` (76 GB since
[D-0233](../../journal/decisions/0233-raise-the-size-cap-to-76-gb-until-phase-4.md))
and the two checkpoint values below. Setting a key is how an operator changes any of them:
`max_database_bytes` (default 32,000,000,000 since D-0186) is the most
`state.sqlite` may be on disk; `recent_window` (default 3) is how many recent
generations of each scope are roots; `collect_older_than` (default one day) is
how old a disposable candidate directory must be before an apply may remove it,
and it must be at least one second, because that floor is the only thing between
a build still writing its candidate and a removal. Every value is checked when
the table is read: a size cap or window below one, a keep-recent below one, an
age floor below one second, and a negative checkpoint age are all refused
([D-0163](../../journal/decisions/0163-every-retention-value-is-checked-where-the-table-is-read.md)).
Doctor reports usage against the first two, and reports bytes in use, file size,
write-ahead log size and free-list bytes separately, because deleting rows frees
pages to SQLite's free list without shrinking the file. Crossing the cap sets
one whole-pipeline operator pause; reading, controls and recovery keep working
([D-0141](../../journal/decisions/0141-two-retention-limits-and-the-collector-age-become-policy.md),
[D-0142](../../journal/decisions/0142-the-size-cap-sets-one-whole-pipeline-pause-and-never-rewrites-one.md)).

The same table carries three values for the local checkpoints the backup
command prunes: `checkpoint_keep_recent` (default 2) is how many of the newest
timer checkpoints stay, `checkpoint_max_age` (default two days) is how young any
other timer checkpoint must be to stay, and `checkpoint_incomplete_max_age`
(default one day) is how long a half-written `run_*` or `.<name>.tmp-<hex>`
directory waits. They are policy for disk on the worker, not for the database,
and they never decide anything about a directory under any other name
([D-0152](../../journal/decisions/0152-the-backup-command-prunes-its-own-checkpoints.md),
[D-0154](../../journal/decisions/0154-policy-removes-only-what-the-code-wrote-and-sizes-it-once.md)).
Production sets `checkpoint_keep_recent = 1` and `checkpoint_max_age = "0s"`:
backups are local only until segmented backups, a checkpoint is about 36 GB,
and the disk cannot hold two with the pipeline's headroom. Each successful
backup keeps the one it wrote and removes every older `run_*`
([D-0203](../../journal/decisions/0203-back-up-locally-and-keep-one-checkpoint-until-segmented-backups.md)).
The runway is short (rough estimate): steady free space is about 218 GB less
twice the state size S, so `[disk-low]` stays on past S ≈ 59 GB and the
backup room check refuses past S ≈ 66 GB. Backup sets replace these
checkpoints once `services.swingset.backupSegmented` is set
([D-0232](../../journal/decisions/0232-segmented-backups-with-a-weekly-restore-drill.md)):
the knobs above do not apply to sets, which keep the newest and the newest
drilled ([backup sets](#backup-sets)).

`gc --apply` frees pages inside the file and `gc --reclaim` gives them back to
the filesystem, which is what the cap measures. Raising `max_database_bytes` is
still the way out when there is nothing to remove, and the pause result says
both.

The limits in force are captured as values in every input bundle, as
`policy/retention.json`, because an absent `[retention]` table means the
defaults and the captured file bytes alone cannot say what the cap was. Changing
a limit changes that file and the digest of `config/sources.toml`, and recomputes
nothing: no stage recipe selects either name, and neither invalidates work. So
raising the cap and deploying never recomputes the history the cap bounds
([D-0156](../../journal/decisions/0156-capture-the-retention-limits-as-values.md)).

Both defaults are provisional. Step 1 of the
[bounded state plan](../plans/bounded-state-and-archive.md) measured a 5.0 GB
file with no recomputation history yet; the numbers and the proposed next steps
are in [D-0166](../../journal/decisions/0166-hold-interning-back-until-rows-repeat-and-cut-indexes-first.md).

Written plans live under `state/gc/plans/`, named by their own fingerprint. A
plan names every artifact file, so the newest ten are kept and older ones are
removed as new plans are written; plans are never copied into a backup.

### Apply

`gc --apply <plan digest>` is the only thing that removes anything. In this
order:

1. Take the writer lock, then the control lock. That order is fixed. Creating a
   hold and admitting a candidate take the control lock too, so no new root can
   appear between the last recompute and the last removal.
2. Recompute the plan while holding both. Stop if the digest differs, having
   changed nothing. A root added between plan and apply therefore stops it.
3. In one transaction: write a note keyed by the plan digest, write the payload
   permission row, remove the eligible payload bytes, remove the permission row,
   commit. The permission row cannot commit, so it has to go first.
4. Still holding both locks, remove the eligible files. This part is safe to
   rerun.
5. Release the locks and write one receipt from the note.

A note is a row in `retention_applies`: the plan digest, the files and payloads
it planned to remove, the payload bytes it removed, when it started, when its
files finished, and its receipt. The note commits before the first file goes,
because SQLite can undo a deleted row and nothing can undo an unlinked file. A
crash between that commit and the last unlink leaves `files_completed_at` NULL.
Notes are permanent and are written once and finished once.

An unfinished note is never replayed on its own. The next apply finishes only the
files the plan it just recomputed under both locks still calls removable; a file
that plan now keeps stays where it is and is listed under `skipped_files`. The
state moves after a crash, so a note is not a plan of the state it is resumed
against, and removal happens only from a plan of the current state. The note is
then closed, and if a skipped file becomes removable again the next plan names it
again. The resuming apply records all of that under `resumed`
([D-0151](../../journal/decisions/0151-a-resumed-note-removes-only-what-the-fresh-plan-still-names.md)).

A receipt accounts for every file its plan named, in exactly one list:
`removed_files`, `skipped_files`, or `already_gone_files` for a file this apply
found was not there. A file the crashed apply had already unlinked counts as
removed in that note's own receipt, because that apply is what removed it
([D-0157](../../journal/decisions/0157-an-operator-report-names-the-items-not-only-the-count.md)).

Rerunning an already applied digest returns the recorded receipt and changes
nothing; it writes the receipt file again if that file is missing, because the
note is the record and the file is the operator's copy. Running an old digest
that was never applied stops, because the state has moved.

No payload is eligible for `gc --apply`. Its gate looks for the
`archived_generations` table of the bounded-state plan's step 4, which phase 4
replaced with packs, so it returns nothing
([D-0149](../../journal/decisions/0149-no-generation-is-eligible-until-a-table-says-it-is-archived.md),
superseded by [D-0229](../../journal/decisions/0229-derivation-output-and-bodies-live-in-packs.md));
payloads leave through `storage apply` instead.
A payload shared by a local generation stays whatever else names it.

The plan carries that answer: every generation row says whether it is archived,
and the totals count them. Apply reads the flag from the plan it was given, so
archiving something after a plan was reviewed moves the digest and stops the
apply, instead of widening what it removes past what the plan named
([D-0155](../../journal/decisions/0155-the-plan-digest-covers-what-is-archived.md)).

### Reclaim

Deleting rows and dropping tables return pages to SQLite's free list and leave
the file the size it was. `gc --reclaim` closes that gap:

1. Check free disk. The in-place rewrite writes a temporary copy of the pages
   in use and, in WAL mode, the same pages again through the log: the phase 4
   rehearsal used 76.3 GB of disk for 32.4 GB in use (2.35 times; W3 of the
   [architecture plan](../plans/architecture.md#9-migration-plan)). So it
   asks `2.5 × bytes in use + 12 GB` (bytes in use is `page_count -
freelist_count` pages) on the smaller of statfs and the quota in
   `/run/swingset-disk`, and refuses before touching anything if that is not
   there or the quota reading is over an hour old (run `sudo swingset-disk`).
   The receipt records this estimate and the old one, twice the file size.
2. Take the writer lock, then the control lock. Point SQLite's temporary files
   at `<state>/tmp` (`PRAGMA temp_store_directory`, put back afterwards; the
   worker's `/tmp` is a small tmpfs). Run `PRAGMA wal_checkpoint(TRUNCATE)`.
3. Run `VACUUM` in place on the one writer connection: no second database file
   and no swap. SQLite's own journal makes it all-or-nothing, so a crash rolls
   back to the original file on the next open. Readers keep reading. A pending
   write on another connection makes it fail, and then it reports that and stops
   with nothing changed
   ([D-0150](../../journal/decisions/0150-reclaim-rewrites-in-place-on-the-one-writer-connection.md)).
4. Run `PRAGMA integrity_check`, `PRAGMA foreign_key_check` and the same schema
   check a checkpoint is verified with, then `wal_checkpoint(TRUNCATE)` again.
5. Release the locks and write one receipt with bytes in use, file size,
   write-ahead log size and free-list bytes, before and after.

Automatic vacuuming stays off. Receipts live under `state/gc/receipts/`, named by
the plan digest for an apply and by when it ran for a reclaim. They are the
operator's copy; the note in the database is the record a restore carries
([D-0148](../../journal/decisions/0148-a-receipt-is-the-operator-copy-of-a-note-that-lives-in-the-database.md)).

Schema 30 adds `finding_support_references(finding_id, kind, sha256)`, where
`kind` is `body`, `extract` or `generation`. Findings declare what they rely on
instead of the closure reading their evidence for digest-shaped strings; the
migration backfills the table from that same scan once, and only a checkpoint of
an older schema still uses it
([D-0139](../../journal/decisions/0139-findings-declare-the-support-they-rely-on.md)).
A writer that declares nothing says nothing: the rows the finding already has,
including the backfilled ones, stay, and an empty declaration is not a change
([D-0160](../../journal/decisions/0160-an-empty-declaration-never-clears-a-findings-recorded-references.md)).
Requirements write their own `findings` row and declare nothing at all; an
`archive_artifact` requirement is about a digest `snapshots.body_sha256` already
pins
([D-0161](../../journal/decisions/0161-a-requirement-finding-relies-on-the-snapshot-pin.md)).

Schema 31 adds `retention_applies`, one permanent note per apply keyed by the
plan digest. Schema 32, interning, comes after both, so an apply on a schema-31
database removes files and no row data at all. Schema 36 adds `storage_applies`,
the same kind of note for `storage apply` and `gc --collect --apply`.

Archiving to remote stores and `gc --restore`, the
[bounded state plan](../plans/bounded-state-and-archive.md)'s step 4, were
never built; packs replaced them ([objects and packs](#objects-and-packs)).
