# Local state

Local state is what the worker keeps between runs: saved evidence, database rows, unfinished work, and release files. This page defines how that state changes and what must survive a restart.

[Reference index](README.md)

## On this page

- [SQLite schema](#sqlite-schema)
- [Registry verification (H1)](#registry-verification-h1)
- [State directory](#state-directory)
- [Invalidation](#invalidation)
- [Identity decision state](#identity-decision-state)
- [Acceptance cases](#acceptance-cases)
- [Requirement inventory in shadow (H11)](#requirement-inventory-in-shadow-h11)
- [Isolated work attempts (H12)](#isolated-work-attempts-h12)
- [Work queue](#work-queue)
- [Event completion persistence (H14 extension)](#event-completion-persistence-h14-extension)
- [Derivation generations (H15)](#derivation-generations-h15)
- [Accepted source generations (H7)](#accepted-source-generations-h7)
- [Durable controls (H13)](#durable-controls-h13)
- [Retention](#retention)

## SQLite schema

One file, WAL mode, `foreign_keys = ON`, schema versioned by numbered
SQL migrations under `state/migrations/`. Names match the published
tables where a table is published. The [schema history](schema-history.md) lists
what each numbered migration changes and links to its exact SQL.

Internal tables:

| Table                 | Key columns                     | Purpose                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| --------------------- | ------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `meta`                | `key`                           | `schema_version`, `installed_at`, current input bundle hash                                                                                                                                                                                                                                                                                                                                                                                             |
| `runs`                | `run_id`                        | `started_at`, `finished_at`, `dry_run`, `summary_json`, `loop` (the owning loop; NULL for the cycle and the CLI); maintain compacts a loop row's summary to a stub after 30 days                                                                                                                                                                                                                                                                        |
| `hosts`               | `host`                          | `next_allowed_at`, `paused_until`, `pause_reason`, `pause_streak`, `robots_sha256`, `robots_fetched_at`, `robots_status`                                                                                                                                                                                                                                                                                                                                |
| `operator_pauses`     | `scope_kind`, `scope_id`        | operator-requested all, host, source, or requirement-kind pause; stable pause ID, actor, reason, revision, creation time and nullable expiry; separate from automatic host pauses                                                                                                                                                                                                                                                                       |
| `host_budget`         | `host`, `day`                   | `requests`, `bytes`                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| `cursors`             | `name`                          | `value`; durable bootstrap and new-id probe positions and miss counts; `registry_probe_started_at`, `registry_probe_last_completed_at`, `registry_probe_next_at` preserve freshness and cadence across restarts                                                                                                                                                                                                                                         |
| `watches`             | `watch_id`                      | every column in [scheduling](scheduling.md#watches) plus `fingerprint`, `extract_version`, `priority`, `created_by_snapshot_id`, `parent_watch_id`, `ever_ok`, current observation snapshot id                                                                                                                                                                                                                                                          |
| `snapshots`           | `snapshot_id`                   | every column in [fetching](fetching.md#archive) plus `via`, `archive_url`, `captured_at`, `observed_at`, `headers_json`, `classification`, `extract_status`, `extract_sha256`, `parse_status`, `parsed_at`, `extract_version`, `parser_version` (versions last attempted)                                                                                                                                                                               |
| `archive_captures`    | `source`, `url`, `timestamp`    | `digest`, `status`, `mimetype`, `length`, `queried_at`, `cdx_query_id`; the Wayback CDX index we hold per source, so capture selection can be redone offline ([backfill](backfill.md#the-wayback-transport))                                                                                                                                                                                                                                            |
| `observations`        | `observation_id`                | `watch_id`, `snapshot_id`, `kind`, `scope_kind` (`source_event`, `dancer`, `source_index`, `calendar`), `scope_id` (a source reference such as `eepro:asc2025`, never a canonical id), `seq`, `extract_version`, `parser_version`, `payload_json`; indexed by `watch_id` and by (`scope_kind`, `scope_id`)                                                                                                                                              |
| `source_event_map`    | `source`, `source_ref`          | `event_id`, `match_method` (`name_date`, `alias`, `override`), `match_confidence`; the projection that resolves observation scopes to events; rewritten whenever index or calendar observations, `event_aliases.csv`, or `source_urls.csv` change                                                                                                                                                                                                       |
| `revisions`           | `name`                          | monotonically increasing counter per set (`observations`, `source_event_map`, `source_events`, `canonical`, `dancers`, `links`, `findings`, `snapshots`), bumped in the writing transaction                                                                                                                                                                                                                                                             |
| `pending_work`        | `stage`, `unit_kind`, `unit_id` | `enqueued_at`; coalesced work for parse, project, and link; presence means unfinished                                                                                                                                                                                                                                                                                                                                                                   |
| `work_queue`          | `stage`, `unit_kind`, `unit_id` | overlay on `pending_work` and derivation currency: `state` (`ready`, `leased`, `waiting_input`, `quarantined`), `attempts`, `error_class`, `input_fingerprint`, lease owner and expiry, `next_attempt_at`, last reason and error; a row exists only while a unit is leased or failing. A leased row has an owner and expiry; a waiting or quarantined row has a clock. Migration 34 seeds rows for failing pending units; see [Work queue](#work-queue) |
| `acquisition_plan`    | `plan_key`                      | `year:<yyyy>` or `page:<watch_id>` rows the plan loop writes and acquire reads ([D-0218](../../journal/decisions/0218-the-loops-judge-the-year-gate-on-facts-and-acquire-from-a-plan-table.md)): `reason` (`eligible` or the gate's stop reason), `rank`, source, URLs, `detail_json`, `planned_at`, `run_id`. A pass cut by its deadline keeps older page rows; a whole pass deletes them                                                              |
| `guard_reasons`       | `subject`, `reason`             | why a fetch is held: subject `host:`, `watch:` or `loop:`, the loop that saw it, `since`, `last_seen_at`, `expires_at`, `seen` ([guards](fetching.md#guards))                                                                                                                                                                                                                                                                                           |
| `loop_state`          | `loop`                          | each loop's latest tick: run, start and finish, `failed`, `error`, `counters_json`, `last_progress_at`                                                                                                                                                                                                                                                                                                                                                  |
| `derivation_building` | `generation_id`                 | a generation whose rows are being stored in chunks after its live write: unit, fingerprint, witness, label digest and rows, `rows_stored`                                                                                                                                                                                                                                                                                                               |
| `accepted_inputs`     | `consumer`, `input_name`        | `digest`; the input value whose invalidation work has been committed, not a stage completion record                                                                                                                                                                                                                                                                                                                                                     |
| `findings`            | `finding_id`                    | `kind`, `subject_kind`, `subject_id`, `watch_id`, `snapshot_id`, `severity`, `summary`, `evidence_json`, `suggested_override`, `opened_at`, `run_id`, `closed_at`, `closed_by`                                                                                                                                                                                                                                                                          |
| `source_events`       | `source`, `source_ref`          | projection of index observations: `name_raw`, `start_date`, `end_date`, `location_raw`, `url`, plus provenance; joins to `source_event_map` for the `event_id`                                                                                                                                                                                                                                                                                          |
| `backup_uploads`      | `path`                          | `sha256`, `uploaded_at`; upload optimization only, verified against the selected archive commit on restore                                                                                                                                                                                                                                                                                                                                              |

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
baseline -> candidates/<candidate_id>
holds/<hold_id>.json           one retention hold: who, why, when, what it keeps
gc/plans/<digest>.json         written retention plans; disposable, not backed up
gc/receipts/<name>.json        one receipt per apply and per reclaim; not backed up
RESTORE_PENDING                present until restore verification succeeds
state.intent, state.lock       reader-writer lock pair (below); never state
heavy.intent, heavy.lock       keeps release builds and derive's units apart
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
GC is manual in v1, removes nothing except through `gc --apply`, from a written
plan, under both locks, and the only files it removes are candidate directories
no release or recent build claims, once they are older than
`retention.collect_older_than`. See [retention](#retention).

### Locks

`state/rwlock.py` owns the protocol
([D-0215](../../journal/decisions/0215-loops-share-the-state-under-a-reader-writer-lock.md)).
Lock files are `flock` locks, never copied into a checkpoint and never removed
by a restore.

- **Exclusive** (the default `open_database` mode): the cycle, every CLI
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
  (waiting up to 900 s); derive takes them shared, non-blocking, and starts
  no unit in a tick that cannot (F10).

Order: `loop-<name>.lock`, `state.intent`, `state.lock`, `control.lock`, SQLite.
Every writable connection waits up to 70 s for SQLite's writer (busy timeout),
the publication boundary's included (`publish/safety._connection`). An
admission waits up to 90 s for `control.lock`, then raises `ControlTimeout`.
Either timeout skips the unit or request and counts nothing.

## Invalidation

`state.work.unfinished_units` defines unfinished work. Parse retains its
durable queue and admission contract. Project and link compare desired inputs
with their materialized generations; their queue rows are scheduling hints.
Deleting a hint cannot hide unfinished work. `accepted_inputs` identifies the
captured inputs available to consumers. Output revisions describe changed
data, never whether another stage ran.

At cycle start, read config, overrides, vocabularies, implementation versions,
and the exact runtime artifact into a validated immutable input bundle. The
bundle also carries `policy/retention.json`, the retention limits in force as
values, because an optional table with defaults cannot be read back from the
captured file alone; see [retention](#retention). Each consumer
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

| Changed input                                                                                                   | Work enqueued in the same transaction                                                                                               |
| --------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------- |
| New snapshot                                                                                                    | Parse that snapshot                                                                                                                 |
| `recipe/env`: interpreter, installed distributions, pyproject's `[project]` table                               | Parse every retained snapshot; queue every project and link unit as a hint; identical output keeps its generation                   |
| A kind's extract or parse code scope, even with unchanged version labels                                        | As that kind's `EXTRACT_VERSION` or `PARSER_VERSION` change below                                                                   |
| A project, link or build code scope                                                                             | Queue that kind's project units, or every link unit, as hints; build none. Identical output keeps its generation                    |
| `EXTRACT_VERSION` for a page kind                                                                               | Parse every archived snapshot of that kind, re-extracting before parsing; invalidate the watch's cached extract fingerprint version |
| `PARSER_VERSION` for a page kind                                                                                | Parse every archived snapshot of that kind from its stored extract                                                                  |
| Successful parse changing current observations                                                                  | Project their old and new scopes; calendar or index changes enqueue the map unit                                                    |
| Event aliases, source URL overrides, event-matching vocabulary                                                  | Map unit: recompute map, source events, watch seeds, and affected old and new event projections                                     |
| Other projection vocabulary or `PROJECTOR_VERSION`                                                              | Map unit and every current or previously materialized canonical scope                                                               |
| Event projection changes                                                                                        | Link that event, including removed subjects                                                                                         |
| Registry dancer or placement projection changes                                                                 | Link every event in v1; a new dancer can match an entry that had no candidate before                                                |
| Weights, nicknames, identity overrides, `LINKER_VERSION`                                                        | Link every event with canonical subjects or existing link rows                                                                      |
| Findings, published snapshot fields, source events, source map, canonical rows, dancers, or link outputs change | Advance their output revisions; build reads these directly                                                                          |

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

Fair offline service rotates stages and unit kinds; initial ties put map
work before other project scopes. A map unit commits the new
map and all event projections affected by membership changes atomically,
then enqueues their link work. It may subsume pending projection work
for those scopes in that same transaction. Ordinary scopes are one
transaction each. Enqueueing includes old scopes so deleting or moving
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
in that cycle, as unit runs already do. Project and link commit their
materialized generation in that transaction too. If a unit cannot complete,
it remains unfinished and downstream stages do not consume partial work.
Parse's handled failures are defined in [parsing](parsing.md#version-changes-and-failures).

Normal build waits for parse, project, and link work to finish, then
receives offline time before unused time is lent back to acquisition.
A budget stop leaves unfinished queues for the next cycle. Pending
publication reconciliation and necessary correction-only builds have
priority before ordinary acquisition and derivation. Build inputs
and artifact completion are owned by [build](build.md#build-inputs). Its
derivation generation records the selected inputs and exact artifact pointer.

The scheduler reserves acquisition time even with downstream work pending,
then rotates eligible offline work fairly. Actual host limits, controls,
and backlog high-water marks govern collection. A cycle excludes attempted
input fingerprints, rather than whole units, so later parses can supply
new inputs to a shared projection without reopening unchanged retries.
See [scheduling](scheduling.md) for the initial allocations and objectives.

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
`requirement_transitions` and `requirement_attempts` survive inventory-row
loss. A work attempt that succeeds with no finding for its requirement writes
no `requirement_attempts` row (D-0213), so a finding's `attempt_count` counts
from its first unmet attempt. `requirement_scan` stores a bounded keyset cursor across all retained
years. Each cycle checks at most 100 scopes before ordinary work. A later
page or pass recreates a deleted requirement from its support. Removed scopes
retire explicitly; they are not counted as successful repairs.

`requirement_cohorts` pins scope, policy, baseline time, whether its universe is
bounded, and the first-open transition cutoff captured atomically with its
membership. Migration 10 adds that nullable cutoff; legacy captures retain
NULL because their within-timestamp ordering is unknown. Membership in
`requirement_cohort_members` never changes
when work is retried, reopened, retired, or newly discovered. H11 executes no
repairs. H15 derivation reporting uses fingerprint comparisons and separately
counts registered scopes with no materialized generation. Older schemas retain
an explicitly labeled queue-based report.

## Isolated work attempts (H12)

Migration 11 adds `work_generations` and `work_attempts`. Queue insertions and
invalidations advance a per-unit generation without changing admission's
existing timestamp token. Explicit retries have a separate generation and a
recorded reason and request time.

`state.attempts.begin_attempt` durably records the unit, queue generation,
retry generation, work token, input fingerprint, run, and start time before
execution. Outcomes are `running`, `succeeded`, `blocked`, `transient`,
`unavailable`, `interrupted`, and `superseded`. Completion retains its time,
stable reason, evidence, retry deadline when applicable, and requirement ID.

Success commits with the unit's output and queue completion. If the queue
changed during execution, the caller rolls back stale output and records
supersession. Failure keeps the pending unit; it cannot consume newer work.
The fingerprint identifies retry inputs: relevant source bytes, source/watch
identity, admission policy, and captured implementation inputs. Queue times,
attempt counts, and attempt findings do not change it. These fingerprints
control retries; H15 owns desired/materialized derivation generations.

Blocked and unavailable work does not retry the same inputs merely because
another cycle or inventory scan runs. Changed relevant inputs or an explicit
`request_retry` releases that latch; the work queue's waiting clock makes that
request when it comes due. Transient and interrupted outcomes have a future
deadline, which the work queue sets. After taking the exclusive lock, the cycle
and the CLI convert abandoned running attempts to interrupted outcomes and
release every lease; they do not infer success. A running attempt whose queue
lease is dead is eligible, and the next lease closes it (see below).

A failed work unit has a `work_attempt` requirement with the exact unit and
fingerprint, outcome evidence, reason, and next action. Its retained
`finding_support` payload lets the bounded scan recreate a deleted inventory
row without resetting eligibility. Restoring an artifact does not satisfy the
work requirement; its output still has to commit. Doctor counts durable
running rows even before a unit has its first finding. Legacy running mirrors
are deduplicated. This records attempt state, not operating-system liveness.

## Work queue

Migration 34 adds `work_queue`, an overlay on `pending_work` and derivation
currency ([D-0216](../../journal/decisions/0216-the-work-queue-leases-backs-off-and-quarantines.md)).
A row exists only while a unit is leased or failing; success deletes it in
the output transaction. `state/queue.py` owns the policy.

- **Lease.** `derive_one` leases its unit in a short claim transaction that
  commits before the admission's capture, as
  `<loop>:<run_id>` for 30 minutes (`cycle` and `cli` for those processes). A
  live lease of another owner skips the unit (`lease_busy`). A lease is dead
  when it expired, when its owner is this process's own loop, or when this
  process holds the exclusive lock. Taking over a dead lease finishes its
  running attempt `interrupted` (`process_interrupted`) and settles its
  admission in the same transaction; the unit then waits on the failure
  clock below instead of running (`process_interrupted` outcome, nothing
  run). The cycle and the `parse`, `project` and `link` commands call
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
  `runs/<run_id>.stop` (SIGTERM, then a kill at the stop timeout).
- **Escaped exception.** An exception that escapes `derive_one` leaves its
  attempt running; the offline loop closes it as a counted
  `unit_exception:<type>` failure (`queue.escaped`), unless that write is
  fenced or busy.
- **Admission failure.** An exception in the admission (capture, attempt)
  rolls it back and records a `unit_exception:*` failure on the
  row alone, with no attempt. Selection honours such a row
  (`queue.holds`): a waiting, quarantined or not-yet-due row holds a unit
  whose latest attempt is absent, succeeded or superseded while its inputs
  are the failed ones. A fence or busy error in the admission, an operator
  pause or a control timeout writes nothing and gives the claimed lease back.
- **Orphans.** The maintain loop deletes rows whose unit has nothing left to
  do: no `pending_work` row, no running attempt, not leased, and for project
  and link units current.
- **Failure class.** The attempt's reason, plus `:<ExceptionType>` for
  `unit_exception`. Failures count while the class and input fingerprint stay
  the same; either change restarts at 1.

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
rows `ready` and calls `request_retry` for a blocked or unavailable unit; the
cycle calls it once per cycle, derive once per tick. A retry of a waiting unit that ends the same
way is a `waiting_repeats`, not a failure. A real supersession
(`inputs_changed`), a `ControlTimeout` in the admission and a fence or busy
error (in the admission or while processing) change nothing: derive writes no
fenced table, and the next tick's recovery closes an attempt left open
uncounted.
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
reason. Aggregate unchanged blockers per cycle to avoid an unbounded record per
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
entry changed every dependent fingerprint once.
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
each other, so each project and link scope holds 168 files and the build
scope 174. The scheduler, the loops and acquisition (`schedule/cycle.py`,
`loops/`, `fetch/client.py`, `history/backfill.py`, `schedule/fairness.py`
and the like) are in no scope: an edit there re-runs nothing. `versions()` and
`repository_identity()` live in `versions.py` for that reason (the cycle
re-exports them). An edit inside a shared module re-runs every project and
link unit; identical output keeps its generation (below), so this costs CPU,
not generations. Parse scopes hold 39 to 47 files, and a non-parser change
re-queues no parse. A shallow entry adds its own bytes and its packages'
`__init__`, not its imports or data files; tests pin which of its imports
fall outside its scope. A parse _retry_ still keys on `version/repository`,
so any deploy re-opens blocked parse attempts without re-parsing others.

The projection order is calendar, source index, and dancer scopes; history
inventory; source mapping; event scopes; then history association and coverage.
Inventory includes occurrence and listing enrichment. Previous inventory output
supports stable IDs but does not trigger its own next generation. An alias move
commits the map and every affected old and new event generation together.
Linking selects event output and the whole registry candidate universe, including
dancers that have never been candidates for the event. Link-owned columns are
excluded from its project dependencies.

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

References are permanent, like generations. Payload bytes are the one derivation
record that can be removed, and only by a transaction that carries its own row in
`derivation_payload_removal_authority`. That row cannot survive its transaction:
its deferred foreign key points at an always-empty table, so a commit that still
holds it fails. A grant therefore cannot be left switched on for later
processes. If one is ever written with foreign keys off, `PRAGMA
foreign_key_check` reports it, which checkpoint verification and recovery
already run, doctor lists it, and opening the database clears it. Nothing in the
pipeline writes the row today.

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
count differs; it reads and writes no stored row, and it does not bring archived
bytes back, which is `gc --restore`'s job. Completion then rechecks the inputs before moving the pointer.
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
is pinned), and its closure groups equal the kept generation's: direct
`derivation` entries and the `selected_*`, `source_observations` and
`calendar_observations` sets. Release closure and retention follow only those,
so every walk over the kept id matches one over the re-run. It then reads the
rows once without writing, fences the inputs again, and compares the digest and
row count with the kept label. On a match it stores the dependency sets, a
`derivation_recipes` row and a `derivation_equivalences` row, and updates only
`desired_fingerprint`; the pointer and the `selected` epoch stay. The group,
`selected_generation()` and the certificate use the kept id. If `canonical`,
`dancers`, `links`, `source_event_map`, `source_events` or `observations`
moved between capture and completion, the replay rewrote live rows, and the
kind's replay epoch moves once the group has certified. Otherwise it stores the
rows from a second read, which must match the first. Build never cuts off.
`events.coverage_tier` belongs to history, so no other scope's output records
it. A calendar, map, event or dancer generation from before phase 1 did, so the
comparison takes that one column out of the kept rows first
(`materialization.moved_columns`,
[D-0212](../../journal/decisions/0212-cut-off-across-the-moved-column-and-held-parses.md));
the build applies history's value last either way. A re-run of a generation it reuses, by fingerprint or equivalence, moves
the replay epoch the same way, and a calendar, inventory or map reuse replays
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
`project/history` or its kept generation has more than `CHUNK_ROWS` (5,000)
rows. The `accept-year` command stages history the same way: its
acceptance commits with history's live rows (A), then B and C run as below;
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
  `finish_attempt(succeeded)`, the admission's settle, `queue.succeed` and the
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
Offline selection then skips that kind only, names it in the cycle summary's
`readiness_errors` and fails the cycle; the other kinds keep running.
`pending_units` counts such a unit as pending, so readers that ask whether
work remains fail closed instead of raising out of the cycle.

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
run/work-attempt/candidate references and active, uncertain or settled state.
`execution_dependencies` records its required sources and requirement kinds.
A request additionally records its host. Local derivations have no host scope.
The short control mutex serializes pause servicing with new admission; output
settlement joins the existing unit transaction without reacquiring that mutex.
A parse, project or link unit that commits its output deletes its admission and
dependency rows in that transaction; only unsettled admissions are read, and
its work attempt keeps the run, times and outcome. Every other outcome and
action kind keeps its settled admission
([D-0213](../../journal/decisions/0213-keep-no-bookkeeping-a-committed-run-does-not-need.md)).
An acknowledged pause fences later admissions while prior work drains.
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
and `write_lock_timeouts` for that run (`write_deadline.run_counters`). Cycle
summaries and loop records also carry `peak_rss_bytes`, the process's peak
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

## Retention

A saved computation is three things with three lifetimes. The **label** (the
`derivation_generations` row, its dependency set, and its row references) is
permanent; nothing here ever deletes one. The **output bytes** are the
`derivation_payloads` rows the references name; retention decides only where
they live. The **current pointer** is `derivation_scopes.materialized_generation_id`,
owned by the pipeline; retention never moves one.

`state/retention.py` does one walk and produces two lists:

- **durable**: every generation that has a label. Its bytes must exist in at
  least one checked place forever. An ordinary superseded generation is on this
  list, so it is never "unknown".
- **local**: every generation the walk reaches from a root. These stay in the
  live database, so every backup carries them and a restore needs no network.

Durable minus local is archivable. Nothing archives or removes output bytes yet:
`gc --plan` writes down what the lists say, and `gc --apply` removes only files
from it. `state/retention_apply.py` is the only thing in the tree that removes
anything
([D-0147](../../journal/decisions/0147-only-a-planned-locked-apply-removes-anything.md)).

### Roots

Roots are read from state that already exists, so no second list can drift:

| Root                | What it pins                                                                |
| ------------------- | --------------------------------------------------------------------------- |
| `baseline`          | The generations the baseline candidate's release closure names              |
| `pending_candidate` | The same, for every pending candidate                                       |
| `pointer`           | Every `derivation_scopes.materialized_generation_id`                        |
| `finding`           | Generations open findings declare in `finding_support_references`           |
| `hold`              | Generations named by a file under `state/holds/`                            |
| `restore_marker`    | The current pointers and pending candidates, while `RESTORE_PENDING` exists |
| `operator_hold`     | The same, while the `operator-hold` file exists                             |
| `window`            | The newest `retention.recent_window` generations of each scope              |

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
present, every payload present, and the count matching the label. A named
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
Production sets `max_database_bytes = "64GB"` and the two checkpoint values
below. Setting a key is how an operator changes any of them:
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
backup room check refuses past S ≈ 66 GB. Phase 4 of the
[architecture plan](../plans/architecture.md) starts, or the quota rises, at
S ≈ 55 GB.

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

No payload is eligible today. Bytes may go only once they are somewhere else,
and the only record that will say so is the `archived_generations` table of plan
step 4, so the gate returns nothing until that table exists and names the
generation
([D-0149](../../journal/decisions/0149-no-generation-is-eligible-until-a-table-says-it-is-archived.md)).
A payload shared by a local generation stays whatever else names it.

The plan carries that answer: every generation row says whether it is archived,
and the totals count them. Apply reads the flag from the plan it was given, so
archiving something after a plan was reviewed moves the digest and stops the
apply, instead of widening what it removes past what the plan named
([D-0155](../../journal/decisions/0155-the-plan-digest-covers-what-is-archived.md)).

### Reclaim

Deleting rows and dropping tables return pages to SQLite's free list and leave
the file the size it was. `gc --reclaim` closes that gap:

1. Check free disk. The in-place rewrite needs about twice the current file size,
   and it refuses before touching anything if that is not there.
2. Take the writer lock, then the control lock. Run
   `PRAGMA wal_checkpoint(TRUNCATE)`.
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
database removes files and no row data at all.

The [bounded state plan](../plans/bounded-state-and-archive.md) owns the rest:
archiving and `gc --restore` do not exist yet, so no payload bytes ever leave.
