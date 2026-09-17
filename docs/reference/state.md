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
- [Event completion persistence (H14 extension)](#event-completion-persistence-h14-extension)
- [Derivation generations (H15)](#derivation-generations-h15)
- [Accepted source generations (H7)](#accepted-source-generations-h7)
- [Durable controls (H13)](#durable-controls-h13)

## SQLite schema

One file, WAL mode, `foreign_keys = ON`, schema versioned by numbered
SQL migrations under `state/migrations/`. Names match the published
tables where a table is published.

Internal tables:

| Table              | Key columns                     | Purpose                                                                                                                                                                                                                                                                                                    |
| ------------------ | ------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `meta`             | `key`                           | `schema_version`, `installed_at`, current input bundle hash                                                                                                                                                                                                                                                |
| `runs`             | `run_id`                        | `started_at`, `finished_at`, `dry_run`, `summary_json`                                                                                                                                                                                                                                                     |
| `hosts`            | `host`                          | `next_allowed_at`, `paused_until`, `pause_reason`, `pause_streak`, `robots_sha256`, `robots_fetched_at`, `robots_status`                                                                                                                                                                                   |
| `operator_pauses`  | `scope_kind`, `scope_id`        | operator-requested all, host, source, or requirement-kind pause; stable pause ID, actor, reason, revision, creation time and nullable expiry; separate from automatic host pauses                                                                                                                          |
| `host_budget`      | `host`, `day`                   | `requests`, `bytes`                                                                                                                                                                                                                                                                                        |
| `cursors`          | `name`                          | `value`; durable bootstrap and new-id probe positions and miss counts; `registry_probe_started_at`, `registry_probe_last_completed_at`, `registry_probe_next_at` preserve freshness and cadence across restarts                                                                                            |
| `watches`          | `watch_id`                      | every column in [scheduling](scheduling.md#watches) plus `fingerprint`, `extract_version`, `priority`, `created_by_snapshot_id`, `parent_watch_id`, `ever_ok`, current observation snapshot id                                                                                                             |
| `snapshots`        | `snapshot_id`                   | every column in [fetching](fetching.md#archive) plus `via`, `archive_url`, `captured_at`, `observed_at`, `headers_json`, `classification`, `extract_status`, `extract_sha256`, `parse_status`, `parsed_at`, `extract_version`, `parser_version` (versions last attempted)                                  |
| `archive_captures` | `source`, `url`, `timestamp`    | `digest`, `status`, `mimetype`, `length`, `queried_at`, `cdx_query_id`; the Wayback CDX index we hold per source, so capture selection can be redone offline ([backfill](backfill.md#the-wayback-transport))                                                                                               |
| `observations`     | `observation_id`                | `watch_id`, `snapshot_id`, `kind`, `scope_kind` (`source_event`, `dancer`, `source_index`, `calendar`), `scope_id` (a source reference such as `eepro:asc2025`, never a canonical id), `seq`, `extract_version`, `parser_version`, `payload_json`; indexed by `watch_id` and by (`scope_kind`, `scope_id`) |
| `source_event_map` | `source`, `source_ref`          | `event_id`, `match_method` (`name_date`, `alias`, `override`), `match_confidence`; the projection that resolves observation scopes to events; rewritten whenever index or calendar observations, `event_aliases.csv`, or `source_urls.csv` change                                                          |
| `revisions`        | `name`                          | monotonically increasing counter per set (`observations`, `source_event_map`, `source_events`, `canonical`, `dancers`, `links`, `findings`, `snapshots`), bumped in the writing transaction                                                                                                                |
| `pending_work`     | `stage`, `unit_kind`, `unit_id` | `enqueued_at`; coalesced work for parse, project, and link; presence means unfinished                                                                                                                                                                                                                      |
| `accepted_inputs`  | `consumer`, `input_name`        | `digest`; the input value whose invalidation work has been committed, not a stage completion record                                                                                                                                                                                                        |
| `findings`         | `finding_id`                    | `kind`, `subject_kind`, `subject_id`, `watch_id`, `snapshot_id`, `severity`, `summary`, `evidence_json`, `suggested_override`, `opened_at`, `run_id`, `closed_at`, `closed_by`                                                                                                                             |
| `source_events`    | `source`, `source_ref`          | projection of index observations: `name_raw`, `start_date`, `end_date`, `location_raw`, `url`, plus provenance; joins to `source_event_map` for the `event_id`                                                                                                                                             |
| `backup_uploads`   | `path`                          | `sha256`, `uploaded_at`; upload optimization only, verified against the selected archive commit on restore                                                                                                                                                                                                 |

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

`state.work.unfinished_units` defines unfinished work. Parse retains its
durable queue and admission contract. Project and link compare desired inputs
with their materialized generations; their queue rows are scheduling hints.
Deleting a hint cannot hide unfinished work. `accepted_inputs` identifies the
captured inputs available to consumers. Output revisions describe changed
data, never whether another stage ran.

At cycle start, read config, overrides, vocabularies, implementation versions,
and the exact runtime artifact into a validated immutable input bundle. Each consumer
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
| Captured runtime artifact changes, even with unchanged version labels                                           | Parse retained snapshots with the new recipe; project and link desired fingerprints change                                          |
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
in the transaction committing its output. Project and link commit their
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
loss. `requirement_scan` stores a bounded keyset cursor across all retained
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
`request_retry` releases that latch. Transient and interrupted outcomes have a
future deadline. After acquiring the process lock, restart converts abandoned
running attempts to interrupted outcomes; it does not infer success.

A failed work unit has a `work_attempt` requirement with the exact unit and
fingerprint, outcome evidence, reason, and next action. Its retained
`finding_support` payload lets the bounded scan recreate a deleted inventory
row without resetting eligibility. Restoring an artifact does not satisfy the
work requirement; its output still has to commit. Doctor counts durable
running rows even before a unit has its first finding. Legacy running mirrors
are deduplicated. This records attempt state, not operating-system liveness.

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
Shared dependency manifests are memoized only within a query, with changes on
the same or another connection invalidating that memoization.

Each selection binds its scope, recipe, ordered dependencies, and context.
Recipes retain package source, SQL schemas and other package data, project lock
inputs when present, interpreter identity, and installed dependency manifest
digests. The captured files remain in the immutable input bundle. Installed
RECORD hashes identify dependencies; they do not audit every installed binary.
Manual version labels remain descriptive. Relevant override and policy inputs
join each stage's recipe; scheduler allocations do not.

The projection order is calendar, source index, and dancer scopes; history
inventory; source mapping; event scopes; then history association and coverage.
Inventory includes occurrence and listing enrichment. Previous inventory output
supports stable IDs but does not trigger its own next generation. An alias move
commits the map and every affected old and new event generation together.
Linking selects event output and the whole registry candidate universe, including
dancers that have never been candidates for the event. Link-owned columns are
excluded from its project dependencies.

Completion rechecks the selected inputs and prior materialized pointer. Output,
owned output-row history, revision bumps, and the new pointer commit together.
A late worker cannot overwrite a newer generation. A crash before commit leaves
the scope unfinished; a crash after commit preserves complete output. Immutable
generations retain exact dependency references even after newer inputs arrive.
Equal inputs with different output are rejected. H12 attempt controls still
govern retries; they cannot make unfinished derivations disappear.

Snapshot extract reuse additionally requires its captured runtime recipe hash.
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
observation writer and downstream invalidation. A source miss is dated absence
evidence, never deletion permission. Revocation records its explicit evidence,
withdraws only that generation's source claims, and cannot be reversed by
retrying the same input fingerprint.

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
An acknowledged pause fences later admissions while prior work drains.

Outer immediate worker transactions enforce a 45-second wall-clock deadline;
savepoints inherit it. Interrupted output rolls back before the attempt records
its blocked outcome. Read snapshots remain available to a concurrently serviced
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
