# Swingset target architecture: independent loops, partitioned releases, bounded state

Adopted 2026-09-25 by the owner ("move the proposal to plans … pause current
work and implement this plan"). Other plans are paused until this one's phases
land, except operating the worker safely. Phase 0 is implemented offline and
not deployed (section 9); nothing else is implemented yet.
Figures come from `docs/status.md`, the
[stall investigation](../../journal/investigations/2026/build-failure-and-history-deadline-2026-09-24.md),
the [derivation investigation](../../journal/investigations/2026/derivation-invalidation-2026-09-23.md)
and the code at candidate 019. The [stability plan](stability.md) holds the
repairs this plan builds on.

## Open decisions for the owner

These change earlier records and are not settled by adopting this plan:

1. **Year acceptance as a publication filter** (sections 8 and 10). Today a
   year must be accepted before its sheets are fetched (D-0180). This plan
   fetches first and withholds unaccepted years at publication. Until the
   owner decides, phase 2 keeps acceptance as an acquisition input in the
   `acquisition_plan` table.
2. **Removing the `event_*` subsystem** (section 8, phase 5). Before any
   removal, a check must show that no published table or column is computed
   from the tables being removed. Removal waits for that check and the owner.

## 1. The problem in one paragraph

Swingset exists to publish a strictly better dataset each week. The current
system is one 837-line function, `run_cycle` in `src/swingset/schedule/cycle.py`,
that runs every stage in sequence inside one process against one 35 GB SQLite
file, with a package-wide code digest as every unit's recipe. That shape is why
one callback mark failed 100 builds, why one 200k-row write closed all fetching,
why every Python edit costs 4 to 9 hours and gigabytes, and why the cycle said
`failed=false` through all of it. The fixes of the last ten days (D-0187 to
D-0201) are correct but each treats one instance of a structural fault. This
document names the structure to move to and the order to move in.

## 2. Invariants

Each rule below names the failure that proves it is needed. A reviewer checks
new code against these, not against the incident list.

| #   | Invariant                                                                                                                                                                                                                    | Failure it answers                                                                                                                                                                               |
| --- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| I1  | **Failure domains are partitions, never the graph.** A unit's blast radius is its own partition (one event, one dancer, one year). No stage may fail the release, the queue or the fetch loop.                               | Budafest 2020 `L-502` failed every release (`build/closure_rows.py`); `ClosureError` escaped and would have aborted acquisition.                                                                 |
| I2  | **Work per step is bounded by construction.** A step touches at most N rows or M bytes, chosen so it finishes in seconds on the smallest partition and the largest. Deadlines are safety nets, never the bounding mechanism. | `project/history/all` wrote 201,073 rows in one transaction and overran the 45 s `bounded_write` (`state/write_deadline.py`); the fix was a 300 s allowance, which still grows with the data.    |
| I3  | **Every non-success has a clock.** Skipped, paused, blocked, waiting and quarantined all carry `retry_at` or `expires_at`, and a reason. The watchdog alarms on any reason older than its expiry.                            | `blocked` had no retry path; `Paused("legacy request spacing unknown")` in `fetch/politeness.py` held six hosts for eight days as a skip (D-0190).                                               |
| I4  | **Health is progress, per loop.** Each loop publishes counters of real work done (pages fetched, generations written, partitions built, commits published). Exit status is not health.                                       | 96 of 100 failing cycles reported `failed=false`; fetching stopped for eight days while cycles finished.                                                                                         |
| I5  | **Loops depend on facts, not on each other's health.** Acquisition reads a plan table. It never calls a derivation predicate.                                                                                                | `phase_two_allowed` in `project/history.py` walked pending derivations (8–10 s each) inside fetch dispatch and the event-site review; a pending history closed the gate that fetched the sheets. |
| I6  | **Every durable byte has an owner, a root set and a collector.** Nothing is written without a rule that removes it once no root reaches it.                                                                                  | 146 unnamed closure proofs, 33.9 GB; the cap was raised 8→32→48→64 GB; `gc --apply` could delete only payloads.                                                                                  |
| I7  | **Work scales with change, not with total data.** A recipe names only the code and inputs a unit reads; identical output is not a new generation.                                                                            | 99.98% of replacing generations were identical; each deploy re-parsed 32,522 snapshots.                                                                                                          |
| I8  | **Effects are idempotent and crash-safe at every boundary.** A retried step produces the same durable result; a killed step leaves only content-addressed, unreferenced bytes that the collector removes.                    | Backup retried into six 26 GB copies (D-0192); failed builds left proofs behind.                                                                                                                 |
| I9  | **The environment is part of the recipe, and the rehearsal environment is the production environment.** Interpreter identity, lock files and code digests are inputs; a rehearsal runs on the worker image.                  | Mac interpreter identity differed from the worker's, so every rehearsal re-queued everything; a 3.13 `PYTHONPATH` leaked into the 3.12 interpreter.                                              |
| I10 | **Memory is bounded per step, not per run.** No step holds a whole table, closure or graph in memory.                                                                                                                        | Build peaked at 5.5–7.4 GB against the 8 GB cgroup; `gc --plan` was killed at 8.3 GB.                                                                                                            |

## 3. Decomposition: five loops, one data plane

Today one process owns everything. The target is five independent systemd
services, each a short-lived process on its own timer, each owning a distinct
set of tables (one writer per table) and reading the others' tables only as
facts. Splitting into files can follow once ownership is clean; it is not
required for the isolation, because SQLite in WAL mode handles several
processes when each write is short (I2), and the existing writer lock in
`state/control_lock.py` already serializes cross-process writes.

| Loop         | Cadence                   | Owns (writes)                                                                           | Reads                                   | Progress counter                              | SLO / alarm                                                                                  |
| ------------ | ------------------------- | --------------------------------------------------------------------------------------- | --------------------------------------- | --------------------------------------------- | -------------------------------------------------------------------------------------------- |
| **acquire**  | every 5 min, 4 min budget | `watches`, `snapshots`, `hosts`, `host_budget`, spacing, `archive_captures`, blob store | `acquisition_plan`, operator pauses     | requests issued per host; snapshots stored    | a host with due work and no `Paused` reason issues ≥1 request per hour; `Paused` past expiry |
| **plan**     | every 30 min              | `acquisition_plan` (which watches and archive pages are eligible, with a reason)        | canonical tables, findings, acceptances | plan rows refreshed                           | plan age < 2 h                                                                               |
| **derive**   | every 5 min, 4 min budget | `work_queue`, generations, payloads, canonical tables, links                            | snapshots, blobs, overrides             | generations written; queue depth by state     | queue oldest ready unit < 6 h; quarantine count change → alert                               |
| **release**  | every 6 h                 | partition artifacts, candidates, `PUBLISHED`, baseline                                  | generations, overrides                  | partitions built/withheld; commits            | a candidate per 24 h; withheld partitions listed; publication strictly better or refused     |
| **maintain** | daily                     | collector marks, checkpoints, restore drills                                            | roots                                   | bytes collected; checkpoint segments uploaded | DB and blob store under budget; last verified backup < 48 h                                  |

**Contracts between loops are tables and content-addressed files, never
function calls.** `acquire` gets its eligibility from `acquisition_plan`,
which `plan` writes with a reason per row (`eligible`, `year_unaccepted`,
`awaits_index`). That table replaces `phase_two_allowed`,
`pending_inventory_projection`, `history/acquisition.py` and the offers pass
in `history/backfill.py`, so a stalled projection can at worst leave the plan
stale (alarm at 2 h), never close fetching (I5). `derive` sees new work through
a `snapshot stored` row it turns into a queue unit, exactly as
`state/work.affected_work` does today for versions. `release` reads only
committed generations by pointer, never live tables.

**Failure domains.** A crash or bug in any loop stops that loop until its next
timer tick; systemd `OnFailure` runs the watchdog, as `nix/module.nix` already
does for the cycle. Within `derive`, the domain is a queue unit; within
`release`, a partition. Operator pause scopes shrink to per-loop, per-host and
per-source flags, which is what `state/controls.py` is used for in practice.

## 4. Data model for scale

### 4.1 Partitions

Every derived fact belongs to exactly one partition, and the partition key is
the unit key:

| Partition          | Contents                                                                                   | Today                                                                                      |
| ------------------ | ------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------ |
| `event/<event_id>` | contests, rounds, entries, heats, callback marks, placements, judges, links for that event | `project/event/*`, `link/event/*` already exist                                            |
| `dancer/<wsdc_id>` | one registry dancer row and their registry placements                                      | `project/dancer/*` exists (29k units)                                                      |
| `year/<yyyy>`      | registry occurrence inventory for the year, series aliases in force, coverage rows         | `project/history/all` and `project/inventory` are global; `history_acceptance` is per year |
| `source_index/<s>` | one source's event listing and its mapping proposals                                       | `project/source_index/*` exists; `project/map/all` is global                               |

The two global units are what make local defects global. `project/history/all`
does two things (`project/history.py` `finalize_history`): set
`registry_placements.event_id` by a series-and-month join, and rebuild the
`coverage` table. The first is a per-year fact: a year partition reads the
registry placements and events of its own year and writes their association.
The second is a release-time reduce over partition outputs and needs no
generation at all. `project/map/all` becomes per-source-index mapping plus a
tiny per-event resolution, because an event's mapping depends only on the
listings that name it. The alias file (`series_aliases.csv`) is an input to
every year partition it dates; an alias edit re-derives the years it touches,
not the graph.

### 4.2 Dependencies at the granularity actually read

`state/derivation_dependencies.py` pre-declares a link unit's dependency as
the whole selected dancer cohort (`selected_project_dancer`, 29k members), so
one dancer page changes every link. The target records the **read set**: while
a unit runs, the data access layer notes which partitions and which override
rows it read, and completion stores their digests as the dependency
fingerprint, plus a reverse index `(read_partition → dependent_unit)`. A change
to dancer 12345 re-queues the events whose linker read dancer 12345, typically
tens, not 2,932. This is how Bazel, Salsa and Shake work; here it needs one
wrapper around the row readers in `link/evidence.py` and `project/*`, and
over-approximation (record a table when in doubt) is the safe direction.

### 4.3 Early cutoff and scoped recipes

Adopt steps 1, 2 and 5 of `docs/plans/derivation-invalidation.md` unchanged:
if a re-run's output digest equals the current generation's, record an
equivalence and move no pointer; a recipe names the digest of the import
closure of the code that stage and kind actually runs, plus a separate
environment digest. These are prerequisites for everything below and are the
last whole-graph re-derivation.

### 4.4 Releases assembled from partition artifacts

Today `build/closure.py` `select` picks one history anchor, then per-event link
generations, and writes a 235 MB manifest of ~33k generations;
`build/closure_rows.py` `reconstruct` then spools every row into a temporary
SQLite file and the builder validates the whole. The target:

1. **Partition artifact.** For each partition whose selected generations
   changed since the last artifact, `release` writes one content-addressed
   Parquet fragment per table (event partitions are small; dancer partitions
   batch by id range into ~64 fragments) with a sidecar listing the generation
   ids, snapshot digests and override digests it was built from. Validation
   runs per artifact: referential checks within the partition, the two
   builder checks that still fail whole releases today (callback counts,
   contiguous places), and support checks against the snapshots it names.
   A failing partition is **withheld** and its reason stored; nothing else
   waits on it (I1).
2. **Release manifest.** A release is the list of partition artifact digests
   plus the withheld list, the overrides digest and the code recipe: a few
   megabytes, not 235 MB, and identical across attempts when nothing changed,
   so retries deduplicate by digest (I8). Cross-partition checks are key-only
   joins over the fragments (entry → dancer id exists; placement → contest
   exists), streamed, not row materialization.
3. **Candidate.** Concatenating fragments per table gives the public Parquet
   files; the `coverage` table is computed here from the manifest.
4. **Publication gate.** The candidate is compared with the baseline by table
   row counts, identity link counts and event count; any decrease needs a
   named override row, or the release is refused with a reason (the missing
   check that let `138352e` ship with zero links). This is where "strictly
   better" becomes a machine check.

**Provenance stays intact and gets cheaper.** D-0022's guarantee, that a
published release is reproducible from its inputs, holds because the HF
commit names the manifest digest, the manifest names each fragment, each
fragment names its generations, and generations name snapshots by hash in the
blob store. Immutability moves from row-level triggers on 130 tables to
"content-addressed files are never rewritten." `closure.hydrate` becomes
"read the manifest by digest."

## 5. Fault tolerance mechanisms

**Work queue with leases.** One table replaces `pending_work`,
`work_generations`, `work_attempts`, `requirement_*` and
`execution_admissions`:

```
work_queue(unit_key PK, stage, partition, state, attempts, lease_owner,
           lease_until, next_attempt_at, last_reason, last_error, enqueued_at)
state ∈ {ready, leased, done, waiting_input, quarantined}
```

A worker leases a unit (`UPDATE ... WHERE state='ready' AND next_attempt_at<=now`,
one row, one short write), runs it, and completes or fails it. Failure sets
`next_attempt_at = now + min(15 min × 2^attempts, 6 h)`; the third failure
with the same error class sets `quarantined` and appends one line to
`ALERTS.log`. Quarantined units appear in `docs/status.md` as a count with a
report command; a code fix re-queues them by error class. `SupersededWorkError`
stays as-is (inputs changed; requeue with attempts unchanged). Leases expire,
so a killed worker's units return to `ready` without a recovery pass;
`state/attempts.recover_interrupted` and `controls.recover_admissions` go away.

**Bounded writes by construction.** A generation is written in chunks of
about 5,000 rows, each its own short transaction into content-addressed
payload storage under a `building` generation id; the final commit flips the
scope pointer in one statement (`derivations.complete` already separates
retention from the pointer flip). A crash leaves unreferenced payloads for the
collector (I6, I8). The `bounded_write` alarm stays as a safety net at 60 s
and its firing becomes an alarm, because under I2 it should never fire.

**Guards fail closed with a clock.** Every branch that returns `Paused`,
`Wait` or `skipped` in `fetch/politeness.py` and `fetch/client.py` writes a
row `(host_or_watch, reason, since, expires_at)`; the watchdog alarms when
`now > expires_at` and the reason is still current, and the daily summary
lists reasons by count. "Legacy spacing unknown" would have alarmed in an
hour, not eight days. Phase 0 alarms on that state and on `Blocked` host
pauses from existing tables; the record itself needs a table and a package
change, so it lands in phase 2.

**Idempotent effects.** Publication already uses expected-parent commits
(`publish/service.py`). Backups become idempotent by naming segments by
content digest (section 6). Fetches are idempotent by the reservation model
already in `politeness.acquire`.

**Backpressure.** Each loop has one knob: `acquire` stops issuing when
`snapshots` without a parse generation exceed a byte budget; `derive` stops
leasing `project` units when the release loop is more than two intervals
behind; `release` refuses to start when free disk is under the scratch it
needs. All three are counters the watchdog can read.

## 6. State and storage

### 6.1 Where bytes live

| Class                                                   | Store            | Roots that keep it                                                         | Collector                             |
| ------------------------------------------------------- | ---------------- | -------------------------------------------------------------------------- | ------------------------------------- |
| Snapshot bodies, extracts, manual artifacts             | blob store (CAS) | every snapshot row (permanent; source evidence is never deleted)           | none; only orphans from failed writes |
| Parse results                                           | blob store       | the accepted generation per snapshot, plus generations a release names     | mark-sweep, weekly                    |
| Derivation payloads                                     | blob store       | current pointer per scope, generations named by the last N releases, holds | mark-sweep, weekly                    |
| Partition artifacts, release manifests                  | blob store       | baseline, pending candidate, last N published releases                     | mark-sweep, weekly                    |
| Labels (generation rows, dependency digests, attempts)  | SQLite           | permanent, small                                                           | none                                  |
| Queue, plan, counters, hosts, watches, canonical tables | SQLite           | live                                                                       | n/a                                   |
| Checkpoints                                             | local + HF       | last 3 verified                                                            | prune after verified upload           |
| Rehearsal scratch                                       | `/var/tmp`       | `KEEP` marker                                                              | existing 3-day timer                  |

The SQLite file then holds only labels, indexes and live canonical rows. The
2026-09-18 measurement had a 5.0 GB file before any recomputation history;
with payloads and manifests in the blob store, 8 to 12 GB is the expected
steady state, and it stops growing per deploy (I7). `state/retention.py`
already models roots and reachability; it shrinks to a streaming mark over
the blob directory with the caches keyed by dependency digest, as the
invalidation plan's step 4 describes.

### 6.2 Backups that fit the transport

The 20 GiB single-file limit in `backup/huggingface.py` is only a problem
because a checkpoint is one archive of everything. With immutable blobs:

- **Blob store:** upload only objects absent from the remote manifest, in
  tar segments of at most 4 GiB, each named by its digest. Daily cost is the
  day's new objects, usually well under 1 GiB.
- **Database:** `VACUUM INTO` a copy (consistent, no writer lock held long),
  compress with zstd, split into 4 GiB parts with a manifest. A 10 GB file
  compresses to roughly 2 to 3 GB.
- **Verification:** `maintain` restores the newest checkpoint into scratch
  once a week, opens it, checks `PRAGMA quick_check` and one published
  release's manifest hydration, then prunes local checkpoints older than the
  verified one. A failed verification is an alert (I3, I4).

The room check from D-0192 stays; it becomes rarely relevant at these sizes.

### 6.3 Capacity model for the 8 GB / 256 GiB worker

| Budget            | Target                | Guard                                     |
| ----------------- | --------------------- | ----------------------------------------- |
| SQLite file       | ≤ 20 GB (alarm at 15) | cap pauses `derive` only, never `acquire` |
| Blob store        | ≤ 80 GB               | collector; alarm at 60                    |
| Local checkpoints | ≤ 60 GB (3 × 20)      | prune after verified upload               |
| Scratch           | ≤ 40 GB               | existing timer                            |
| Headroom          | ≥ 50 GB               | watchdog `diskMinFreeBytes`               |
| Memory per step   | ≤ 1.5 GB resident     | per-partition builds; streaming collector |

### 6.4 SQLite or PostgreSQL

SQLite stays adequate for one machine and five short-write processes: the
queue is a few hundred thousand rows, the labels a few million, and all long
operations move to files. Move to PostgreSQL when any of these is true: a
second worker host is needed for fetch parallelism (host politeness makes this
unlikely; the limit is politeness, not CPU); writers measurably wait on each
other after chunking (measure `busy` waits per loop); or the live file exceeds
50 GB with the collector running. The existing `docs/plans/postgres-migration/`
stays parked, and section 4 makes its M3 import audit meaningful, because it
would import 10 GB of live rows instead of 40 GB of redundancy. The
investigation's judgment stands: the planner died of Python heap, not of SQLite.

## 7. Deployability

- **Cost proportional to change.** Scoped recipes (4.3) mean a change to
  `history/closure.py` re-queues nothing and a change to `sources/eepro`
  re-parses EEPro pages only. Output cutoff means even those write no new
  generation when bytes match.
- **Versioned interpreters.** `EXTRACT_VERSION` and `PARSER_VERSION` labels
  (`schedule/cycle.versions`) are replaced by the code digest per page kind;
  the hand-written numbers can no longer be forgotten.
- **Shadow cycles on the worker image.** A `swingset-shadow` unit takes a
  reflink copy of the live state (btrfs `cp --reflink`, seconds, no extra
  space until written), runs the candidate package's `derive` and `release`
  loops against it with fetching disabled, and reports: units re-queued,
  generations whose output changed, partitions withheld, peak memory. The
  same interpreter, lock file and cgroup as production (I9). Deploy proceeds
  only when the report matches the change's intent ("re-queues 1,204 EEPro
  parses, changes 3 outputs").
- **Progressive rollout inside one worker.** Canary by partition: the new
  interpreter runs on 5% of event partitions first, their outputs are
  compared with the previous generation's digests, and the rest follow on a
  clean diff or a reviewed one. This is cheap once outputs are content
  addressed, and it replaces the day-long edit-deploy-observe loop.

## 8. Simplification

Line counts are from the working copy. Removal follows the phases in
section 9; nothing is deleted before its replacement runs.

| Subsystem                                              | Lines | Judgment                                                                                                                                                                                                                                                                                                                                                                                                                             |
| ------------------------------------------------------ | ----- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `schedule/event_*.py` and ~40 `event_*` tables         | 5,622 | Over-engineered. Pressure, progress, timing, blockers, accounting, retirement, capacity and turns are eight ways of observing scheduling from inside the scheduler, most gating acquisition on derived observations (`event_pressure_bootstrap`, `event_progress_refresh`). They caused the D-0201 overrun path. Replace with the queue's counters, one per-event progress view and `acquisition_plan`. Keep `event_turns` fairness. |
| `state/requirement*.py`, `requirement_*` tables        | 911   | Duplicates the queue: a requirement is a pending unit with a reason. Fold into `work_queue.last_reason` and the quarantine report.                                                                                                                                                                                                                                                                                                   |
| `state/controls.py`, `control_*`, `execution_*` tables | 1,028 | Keep the operator pause with scopes (host, source, loop) and the writer lock. Drop the admission ledger and `recover_admissions`; leases make them redundant.                                                                                                                                                                                                                                                                        |
| `history/*`                                            | 4,592 | Keep the catalog, CDX intake and Wayback dispatch (the acquisition path that actually finds old sheets). Move year acceptance from an acquisition gate to a publication filter (unaccepted years build but are withheld). `event_sites.py` review and `origin_dispatch.py` become offline tools under `journal/tools/`; neither belongs in a 4-minute loop.                                                                          |
| `admission/*`                                          | 4,433 | Split. The per-source contracts (`round_contract`, `stepright_contract`, `registry_contract`) that detect layout changes are valuable and stay with parsing. The release-time evidence re-verification (`page_evidence`, `parent_evidence`, `evidence_budget`, `enumeration_evidence`) is replaced by per-partition support checks that run once per artifact change (4.4), removing the load-dependent 30 s budget.                 |
| `derivation_dependency_sets` member manifests          | —     | Replaced by read-set digests and a reverse index (4.2). The 235 MB proof and the shared 29k-member cohort sets go with it.                                                                                                                                                                                                                                                                                                           |
| `run_cycle`                                            | 837   | Becomes five loop entry points of under 100 lines each; the phase-share allocator in `schedule/fair_policy.py` is unnecessary when loops have their own timers.                                                                                                                                                                                                                                                                      |

Expected net: roughly 12,000 to 15,000 of 52,000 lines removed, about 50 of
130 tables, and the core suite faster. What stays is the part that produces
the dataset: sources and parsers, projection, linking, per-partition build,
publication, blob store, backup.

## 9. Migration plan

Ordered by risk reduced per unit of effort. Effort is agent time calibrated
on the last two weeks (a one-file production fix: 2 to 4 hours; invalidation
plan steps: 1 to 3 days each). Each phase is a switch with a rollback to the
previous system generation and a byte copy of the database, as the stability
plan's production sequence already does. Phases 1 to 3 each change Python and
each costs a whole-graph re-derivation until phase 1 lands, so phase 1 goes
first and alone.

**Stop doing now (phase 0, 1 to 2 days).**

- No new `event_*`, requirement or control features; no new global units.
- No cap raises. If the file grows, find the writer.
- Disable the event-site review and the pressure and progress refreshes by
  config (they are optional stages), removing the D-0201 path entirely.
  _Implemented, not deployed_
  ([D-0204](../../journal/decisions/0204-turn-off-the-event-bookkeeping-stages-and-open-the-pressure-gate.md)):
  `[stages]` in `config/sources.toml` turns off the site review, pressure,
  progress refresh and blocker observations, and pressure off opens its gate.
  The switches are package code (a whole-graph re-derivation), so they ship
  with phase 1's switch; the 019 package ignores the table. Turning pressure
  back on, which a rollback to 019 also does, needs no command: the cycle's
  pressure bootstrap drains the dirty rows the triggers kept, 100 watches a
  cycle, holding new-event admission until they drain. Starts whose request
  stored no page stay uncounted until that watch is fetched again.
- Watchdog on progress: alarm when `max(fetched_at)` or the newest generation
  is older than 2 hours while unheld. _Implemented, not deployed_
  ([D-0202](../../journal/decisions/0202-the-watchdog-alarms-on-progress-within-two-hours.md)):
  keyed lines repeated at most every 2 hours; fetching measured by requests,
  not stored pages, with a budget-spent exception; progress per stage with
  ready work, clocked from when the work became runnable (links: when history
  materialized after their last progress, or came due and did not run;
  `project/history` clocked from when it came due); the size-cap pause; the
  two known silent gates (legacy spacing unknown, `Blocked` host pause) and a
  host answering only 429 or 5xx; an overdue hold, an inactive timer, a failed
  backup; and `[watchdog-blind]` for any check that could not run twice. Every
  query is tested against the real migrations. The general guard record and a
  clock for blocked units stay in phase 2.
- Remote backups: pause the upload with a standing alert line, keep the
  verified Mac copy current weekly by hand, until phase 4. Backups are refused
  (the 12:00 UTC check on 2026-09-25 wanted 119 GB; the 16:10 UTC preflight
  receipt shows 46.5 GB free) because local checkpoints are pruned only after
  an upload that always fails. Prune the oldest timer checkpoints after an
  independent check, since it deletes data, and let a local-only backup prune
  its own predecessors. _Implemented, not deployed_
  ([D-0203](../../journal/decisions/0203-back-up-locally-and-keep-one-checkpoint-until-segmented-backups.md)):
  `backupUpload = false` on orb runs `backup --local` with a one-copy room
  check, `[retention]` keeps one timer checkpoint, and the watchdog adds
  `[backup-upload-paused]` (daily), `[backup-stale]` (36 h) and
  `[backup-failed]`. A local backup verifies its new copy (`verify_checkpoint`
  with `quick_check`) after releasing the writer lock and before it prunes; a
  failed copy is renamed `failed-run_*`, replacing any older one, its run
  record says failed, and the backup fails without pruning (package code,
  ships with 0b). On a clone of the 16:10 copy on the Mac the read-back took
  about 6 minutes (`quick_check` 199 s, `ok`) and the whole backup about 10,
  at 320 MB peak memory; the worker may be slower, and the unit stops a local
  backup after 6 hours. The one-time prune of old `run_*` checkpoints stays an
  operator `rm` after the independent check, and the switch comes after it:
  whenever more than about 69.5 GB is free, the one-copy room check passes and
  the first timer run would otherwise prune unchecked
  ([local-only backups](../guides/operation.md#local-only-backups)).
- Two changes. 0a is `config/`, `nix/`, the watchdog, docs and records, and
  deploys on the 019 package with no re-queue. 0b is the package code and its
  tests, and ships with phase 1's switch. `tests/test_config.py` and
  `tests/backup/test_backup_prune_wiring.py` split by hunk: their stage and
  read-back tests go with 0b. The 0a tree alone passes the core suite.
- Deploys: batch, and diff the deploy tree's `src/` against the deployed
  store before every switch.

_Phase 0 status: implemented offline 2026-09-25, not deployed._ Acceptance
ran on APFS clones of the 16:10 UTC production copy on the Mac, with no
network (a mock transport answers 304 or 404), one
[receipt](../../journal/evidence/runtime/architecture-phase0-rehearsal-2026-09/receipt.json):

| Check                          | Result                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| ------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| R0, module eval                | `backupUpload = false` gives `backup --local`, a one-copy room check, a 6 h timeout and the watchdog's `--backup-upload 0`; `true` keeps two copies, no timeout and no backup flags.                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| R1, 0a re-queues nothing       | 019 code. After a baseline accept (the Mac interpreter re-queues 33,906 parse units), accepting the 0a config changed only `config/sources.toml` and `policy/retention.json`; pending work stayed link/event 2,932, parse/snapshot 33,906, project/history 1 (2.0 s). `config/sources.toml` is in the project work fingerprint, so a pending project unit whose latest attempt is blocked would become eligible again (a retry, not a re-queue); the copy has none.                                                                                                                                                                               |
| R2, one 900 s cycle, 0b vs 019 | Parent code and config against the working copy with `[stages]` off, each on a fresh clone. Both `failed=false`, no stage errors, one parse failure each (the same page). The new summary lacks the five stage keys and lists the four in `stages_disabled`; reconciliation 1.7 s against 2.6 s (the site review had nothing to review). Backpressure from the Mac re-queue held all requests in both, so a second pair lifted it by config: (new against 019) 170 requests against 152, no pressure denials against 2,748, no `event_index` watch due in either, first request after about 9 minutes in both, peak memory 4.3 GB against 4.9 GB. |
| R3, watchdog on real data      | At 16:10Z: `[stale-fetch]` (3 h) and `[stale-progress]` (link 2 h 58 m with 2,932 ready; project/history). At 13:30Z: nothing. With `--backup-upload 0`: also `[backup-stale]` and `[backup-upload-paused]`. 0.04 to 0.27 s a run.                                                                                                                                                                                                                                                                                                                                                                                                                |
| R4, local backup, working copy | 407 s, 331 MB peak, a 35.8 GB checkpoint; read-back `ok` in 294 s; both fake `run_*` checkpoints pruned, the operator-named one and the new copy kept.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| Tests                          | Core suite 1,151 passed; the full suite passed 2,606 in the second review; `ruff` and `mypy` clean. The 0a tree alone (019 `src/`) passed its core suite in review.                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |

Conditions from review, carried to the switches:

- Split the working change into a 0a change and a 0b change before phase 1
  edits the shared files. The 0a tree's `src/` must equal `1b95eff`; re-run
  R1 on it before the 0a switch.
- 0a deploys on the 019 package, which has no read-back. From the first timer
  backup after the switch, each unchecked new copy deletes its predecessor.
  The Mac byte copy must be current and verified before the switch and
  refreshed weekly after; the deletion's independent check acknowledges this
  window.
- 0b ships only with phase 1's switch. A rollback to 019, or turning pressure
  back on, closes `event_index` expansion on every host until the dirty rows
  drain (about 5 hours per week the stage was off), and busy hosts can stay
  latched high after that. Phase 1's rollback plan names this, and the
  operator watches `event_index` requests by host for a day after
  ([stage switches](../reference/scheduling.md#stage-switches)).
- Phase 1's post-check records the worker's `checkpoint-verify` seconds and
  the state size without `checkpoints/` against the 55 GB phase 4 trigger.

Deploy steps (operator; the owner is
[local-only backups](../guides/operation.md#local-only-backups)):

1. 0a: build the deploy tree from the 0a change and diff its `src/` against
   the deployed store (must be empty). Refresh and verify the Mac byte copy;
   preflight receipt.
2. Independent check, with a written verdict, of the deletion list: every
   `run_*` but the newest, plus the `blobs/` and `extracts/` closure check.
3. Stop `swingset-backup.timer`. Remove the approved directories as root,
   then `systemctl reset-failed swingset-backup.service`; receipt.
4. `nixos-rebuild switch` to the 0a tree, start the timer; post-check receipt
   (next backup writes one copy and prunes its predecessor; pending counts by
   stage unchanged after the first cycle; `[backup-upload-paused]` once).
5. 0b: with phase 1's switch, under its hold, byte copy and receipts; its
   post-check adds `stages_disabled` in cycle summaries, no stage errors from
   the four stages, and `event_index` requests on formerly latched hosts.

Deploy cost: 0a is one rebuild of minutes, no re-queue, no migration, one
independent check, one manual deletion of about 94 GB, and a 35 GB Mac copy.
Rollback is the previous system generation, which brings back refused
backups. 0b adds nothing to phase 1's switch. Alone it would cost a
whole-graph re-derivation (33,906 parses, 4 to 9 hours, several GB). After
it, each local backup adds a read-back of about 5 minutes on the Mac, likely
longer on the worker.

**Phase 1: pay the last re-derivation (6 to 9 days).** Invalidation plan steps
1, 2 and 5 (output cutoff at derive and parse, scoped recipes) in one switch,
plus the equivalence table (migration 33). Its post-check receipt also carries
0b's: the first local backup's `checkpoint-verify` result and seconds, and the
state size without `checkpoints/` against the phase 4 trigger. Its rollback
plan names the pressure drain that a return to 019 starts (phase 0
conditions). _Accept when_ a
deploy touching no parser re-queues zero parse units (receipt), a re-run with
identical output writes no generation, and the file grows by under 100 MB
across a code-only deploy.

**Phase 2: split the cycle and fix the queue (about 2 weeks).** Five systemd
units; `acquisition_plan` written by `plan` and read by `acquire`;
`work_queue` with leases, backoff and quarantine; chunked generation writes;
per-loop progress counters and the reason-with-expiry table for guards; the
`bounded_write` alarm demoted to a safety net. The event-site review and the
history gate are deleted from the acquisition path in this phase. _Accept
when_ `kill -9` of any loop mid-write leaves the other four progressing on
the next tick; a unit that raises three times is quarantined with one alert
line and the queue keeps draining; `acquire` issues origin and Wayback
requests while `derive` holds blocked units; no `bounded_write` alarm fires
in a week.

**Phase 3: partition the globals and the release (about 3 weeks).**
`project/history/all` becomes `year/<yyyy>`; `project/map/all` becomes
per-source-index plus per-event resolution; link units record read sets with
a reverse index; `release` builds partition artifacts with per-partition
withholding, a small manifest, streamed key joins, the strictly-better gate,
and the `coverage` reduce. Retain the old closure path behind a flag for one
release. _Accept when_ one corrupt event withholds only itself in a build; a
release build peaks under 2 GB resident and finishes under 10 minutes; a
single fetched score sheet re-derives one event, its links and its year, under
one minute of derive time; the manifest is under 5 MB and identical across two
attempts with unchanged inputs; a release that would drop identity links is
refused with a reason.

**Phase 4: storage lifecycle and backups (about 2 weeks).** _Trigger:_ start
it, or raise the quota, when the state without checkpoints passes about 55 GB;
one local checkpoint then leaves little room (D-0203). No alarm watches it yet
(maintenance queue); post-check receipts carry the size. Payloads, parse
results and manifests to the blob store with a lifecycle row per class;
streaming mark-sweep across all classes; segmented incremental backup with
weekly automated restore verification; migration of retained
`derivation_payloads` out of the file, then reclaim. Deletes data, so it takes
the independent check. _Accept when_ the file is under 15 GB, the daily backup
uploads and verifies, the restore drill passes on the worker in scratch, and a
month of receipts shows the blob store within budget.

**Phase 5: removals (about 2 weeks, one subsystem per switch).** The table
in section 8, each behind its replacement's receipts, with tests deleted in
the same change. The `event_*` dependency report also covers the writers
that outlive the phase 0 stage switches: the migration 0019 pressure
triggers and the `event_stage_operations` rows appended per fetched or
parsed snapshot. _Accept when_ core suite time drops, the table count is under
80, and the code map fits the five loops.

**Phase 6, triggered, not scheduled.** PostgreSQL per the triggers in 6.4.

About eleven to thirteen weeks of agent time in sequence, less where phases 3
and 4 overlap (different files). Publication resumes when the data-quality
plan's identity recovery ships, which phase 3's gate makes safe to automate.

## 10. Risks, trade-offs and rejected alternatives

**Risks.**

- _Cross-partition facts._ An alias moves an event between series, and a
  registry placement's association depends on it. Year partitions take the
  dated alias rows as input, so the move re-derives the years it names; a
  move across years re-derives two. Tests must cover an alias spanning a year
  boundary.
- _Read-set tracking misses a read._ A raw SQL path that bypasses the wrapper
  under-declares a dependency and a stale link survives. Mitigation: the
  wrapper is the only reader in `project/` and `link/` (a test asserts no
  direct `conn.execute` against canonical tables in those packages), and a
  weekly `verify` in `maintain` re-derives a 1% sample and alarms on a digest
  mismatch.
- _Multi-process SQLite contention._ Five writers with short transactions is
  well within SQLite's design, but the first weeks need the `busy` counter
  and a 5 s `busy_timeout`. If the counter climbs, the fallback is separate
  files per loop, which the one-writer-per-table rule makes mechanical.
- _Weakening D-0022._ Per-partition support checks verify the same snapshot
  hashes, once per artifact change instead of once per build; the guarantee is
  the same, the cost is lower. Do not drop the check; move it.
- _The migration is itself Python changes._ Phases 2 and 3 re-queue only
  what their scoped recipes name once phase 1 is in; phase 1 itself is the
  one remaining whole-graph replay.

**Trade-offs accepted.** Published `first_seen_at`/`last_seen_at` become
"when these bytes were first derived" (the invalidation plan's noted loss).
Year acceptance no longer blocks fetching, so a few hundred pages for an
unaccepted year may be fetched before review; they cost politeness budget,
not correctness, because publication still withholds them. Five processes are
harder to read in a journal than one; per-loop counters and `run_id` per loop
tick answer that.

**Rejected.**

- _Full rewrite._ The parsers, projection, linker and publication (roughly
  20k lines) are the product and work. The failures are in orchestration,
  invalidation and storage, which the phases replace in place.
- _A workflow engine (Temporal, Dagster, Prefect)._ They solve retries,
  leases and visibility, which is one table here, and they cost a server, a
  database and memory on an 8 GB VM. The hard problems (partition boundaries,
  read-set dependencies, per-partition releases) are not orchestration
  problems.
- _PostgreSQL first._ It moves 40 GB of redundant rows into a server that
  still cannot delete them, and it makes the M3 audit meaningless. It stays a
  triggered option.
- _Keeping one cycle with more `contained()` blocks._ That is the current
  trajectory: each incident adds a guard, each guard a new never-run path.
  Independent loops make containment structural.
- _Raising the cap or the deadline again._ Both scale with the data and
  neither addresses a cause.
