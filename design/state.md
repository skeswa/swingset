# Local state

## SQLite schema

One file, WAL mode, `foreign_keys = ON`, schema versioned by numbered
SQL migrations under `state/migrations/`. Names match the published
tables where a table is published.

Internal tables:

| Table | Key columns | Purpose |
|---|---|---|
| `meta` | `key` | `schema_version`, `installed_at`, current input bundle hash |
| `runs` | `run_id` | `started_at`, `finished_at`, `dry_run`, `summary_json` |
| `hosts` | `host` | `next_allowed_at`, `paused_until`, `pause_reason`, `pause_streak`, `robots_sha256`, `robots_fetched_at`, `robots_status` |
| `operator_pauses` | `scope_kind`, `scope_id` | operator-requested all, host, or source pause; nullable expiry, reason; separate from automatic host pauses |
| `host_budget` | `host`, `day` | `requests`, `bytes` |
| `cursors` | `name` | `value`; durable bootstrap and new-id probe positions and miss counts; `registry_probe_started_at`, `registry_probe_last_completed_at`, `registry_probe_next_at` preserve freshness and cadence across restarts |
| `watches` | `watch_id` | every column in [scheduling](scheduling.md#watches) plus `fingerprint`, `extract_version`, `priority`, `created_by_snapshot_id`, `parent_watch_id`, `ever_ok`, current observation snapshot id |
| `snapshots` | `snapshot_id` | every column in [fetching](fetching.md#archive) plus `via`, `archive_url`, `captured_at`, `observed_at`, `headers_json`, `classification`, `extract_status`, `extract_sha256`, `parse_status`, `parsed_at`, `extract_version`, `parser_version` (versions last attempted) |
| `archive_captures` | `source`, `url`, `timestamp` | `digest`, `status`, `mimetype`, `length`, `queried_at`, `cdx_query_id`; the Wayback CDX index we hold per source, so capture selection can be redone offline ([backfill](backfill.md#the-wayback-transport)) |
| `observations` | `observation_id` | `watch_id`, `snapshot_id`, `kind`, `scope_kind` (`source_event`, `dancer`, `source_index`, `calendar`), `scope_id` (a source reference such as `eepro:asc2025`, never a canonical id), `seq`, `extract_version`, `parser_version`, `payload_json`; indexed by `watch_id` and by (`scope_kind`, `scope_id`) |
| `source_event_map` | `source`, `source_ref` | `event_id`, `match_method` (`name_date`, `alias`, `override`), `match_confidence`; the projection that resolves observation scopes to events; rewritten whenever index or calendar observations, `event_aliases.csv`, or `source_urls.csv` change |
| `revisions` | `name` | monotonically increasing counter per set (`observations`, `source_event_map`, `source_events`, `canonical`, `dancers`, `links`, `findings`, `snapshots`), bumped in the writing transaction |
| `pending_work` | `stage`, `unit_kind`, `unit_id` | `enqueued_at`; coalesced work for parse, project, and link; presence means unfinished |
| `accepted_inputs` | `consumer`, `input_name` | `digest`; the input value whose invalidation work has been committed, not a stage completion record |
| `findings` | `finding_id` | `kind`, `subject_kind`, `subject_id`, `watch_id`, `snapshot_id`, `severity`, `summary`, `evidence_json`, `suggested_override`, `opened_at`, `run_id`, `closed_at`, `closed_by` |
| `source_events` | `source`, `source_ref` | projection of index observations: `name_raw`, `start_date`, `end_date`, `location_raw`, `url`, plus provenance; joins to `source_event_map` for the `event_id` |
| `backup_uploads` | `path` | `sha256`, `uploaded_at`; upload optimization only, verified against the selected archive commit on restore |

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

## State directory

```text
state.sqlite
blobs/                         content-addressed source and manual bodies
extracts/                      content-addressed serialized extracts
inputs/<hash>/                 captured config, overrides, vocabularies, versions
candidates/<candidate_id>/      data/, README.md, _meta/, BUILT, PUBLISHING, PUBLISHED
baseline -> candidates/<candidate_id>
RESTORE_PENDING                present until restore verification succeeds
runs/
venv/                          disposable; rebuilt from the lock
uv-cache/                      disposable
```

Publication markers and the baseline have one owner,
[publish](publishing.md#candidate-and-baseline). There is no SQLite row
claiming a candidate directory exists. Raw bodies, extracts, and input
bundles are written and made durable before the transaction referring
to them. A crash can leave an unreferenced artifact; it cannot leave a
committed reference to an incomplete file. GC removes only artifacts
older than a day that no snapshot, finding, input bundle, candidate,
or retained backup references. GC is manual in v1.

## Invalidation

There is one definition of unfinished parse, project, or link work:
`pending_work`. Stage-wide fingerprints do not gate these queues.
`accepted_inputs` only records that the work for an input change has
been scheduled. Output revisions describe changed data, never whether
another stage ran.

At cycle start, read config, overrides, vocabularies, and implementation
versions once into a validated immutable input bundle. Each consumer
uses those captured bytes throughout the cycle, including build. The
checkout stays the source of corrections; saved bundles provide exact
replay and backup. A later checkout edit is accepted next cycle. Capture
failure leaves the previous bundle and queues untouched and fails the
run. Manual stage commands perform this same acceptance step.

One transaction accepts the bundle, enqueues the union of work affected
by its changed inputs, and updates `accepted_inputs`. A crash before commit does neither; a crash
after commit leaves the work to drain. Repeated invalidations coalesce
by work key. Existing work runs against the newest accepted bundle;
accepting another bundle enqueues its full affected set, including units
already processed under the previous bundle.

| Changed input | Work enqueued in the same transaction |
|---|---|
| New snapshot | Parse that snapshot |
| `EXTRACT_VERSION` for a page kind | Parse every archived snapshot of that kind, re-extracting before parsing; invalidate the watch's cached extract fingerprint version |
| `PARSER_VERSION` for a page kind | Parse every archived snapshot of that kind from its stored extract |
| Successful parse changing current observations | Project their old and new scopes; calendar or index changes enqueue the map unit |
| Event aliases, source URL overrides, event-matching vocabulary | Map unit: recompute map, source events, watch seeds, and affected old and new event projections |
| Other projection vocabulary or `PROJECTOR_VERSION` | Map unit and every current or previously materialized canonical scope |
| Event projection changes | Link that event, including removed subjects |
| Registry dancer or placement projection changes | Link every event in v1; a new dancer can match an entry that had no candidate before |
| Weights, nicknames, identity overrides, `LINKER_VERSION` | Link every event with canonical subjects or existing link rows |
| Findings, published snapshot fields, source events, source map, canonical rows, dancers, or link outputs change | Advance their output revisions; build reads these directly |

The affected set can be empty: map work is needed only when calendar
or index observations, existing source events or mappings, or source
URL overrides exist. Accepting empty files in a fresh state records
their digests without inventing scope work.

`state/work.py` owns this dependency table. Source adapters do not decide
which later stages run. A write, its revision bumps, and its downstream
work are committed together. Only actual output changes bump revisions.
Project-owned column changes advance `canonical` (or `dancers` for the
registry scope, including registry placements); link-owned columns and
link tables advance `links`. Link never advances a revision to relay an
unrelated projection change to build.

Project drains map work before other scopes. A map unit commits the new
map and all event projections affected by membership changes atomically,
then enqueues their link work. It may subsume pending projection work
for those scopes in that same transaction. Ordinary scopes are one
transaction each. Enqueueing includes old scopes so deleting or moving
an observation also removes its former canonical rows.

Link drains one event at a time, applying assignment, link rows,
candidates, and linked columns in one transaction. It does not dirty
itself through its own writes. Every unit deletes its pending row only
in the transaction committing its output. If a unit cannot complete,
its row stays pending and downstream stages do not consume partial work.
Parse's handled failures are defined in [parsing](parsing.md#version-changes-and-failures).

Build waits for parse, project, and link queues to drain. A budget stop
leaves those queues for the next cycle and skips new build and publish;
an already pending publication can still be reconciled. Build inputs
and completion are owned by [build](build.md#build-inputs), not by a
second generic stage-completion table.

The scheduler services existing downstream work before adding another
fetch batch. During a sweep, a fetch batch can exhaust the cycle budget;
the next cycle drains its work before fetching more. Thus continuous
fetching cannot starve projection, linking, or publication.

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
- A fetch batch filling the budget cannot prevent its downstream work
  from completing in later cycles before another batch starts.
