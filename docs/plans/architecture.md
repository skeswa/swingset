# Swingset target architecture: independent loops, partitioned releases, bounded state

Adopted 2026-09-25 by the owner ("move the proposal to plans … pause current
work and implement this plan"). Other plans are paused until this one's phases
land, except operating the worker safely. Phases 0 and 1 are implemented
offline and not deployed (section 9); nothing else is implemented yet.
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
3. **What happens first as the 64 GB cap nears** (section 9, phase 1
   capacity). The 0a receipt (2026-09-26 21:35 UTC) measured a 46.6 GB file
   growing about 5 GB a day, 63.6 GB of state without `checkpoints/`, and
   121.3 GB of quota free. The 55 GB phase 4 trigger has passed. Under 019
   the file reaches the cap, which pauses the pipeline, about 2026-09-30;
   after phase 1's switch, at an estimated 1.5 to 2 GB a day, about ten days
   after it. Local backups need free space of the state plus 20 GB and lose
   margin at two to three times the daily growth, so they are refused within
   days of the switch. The choices: (a) raise the cap under a record amending
   D-0208; (b) start phase 4 or invalidation step 3 now; (c) remove the three
   named checkpoints (`h16-published-20260917`,
   `pre-regression-schema32-20260921T0423Z`, `pre-schema32-20260921`,
   31.8 GB) after an independent check. Phase 1's step 3 needs this call
   recorded before its go.

Phase 1's growth target, an earlier open decision, was settled on 2026-09-26
under D-0183: trim the per-unit bookkeeping at its cause
([D-0213](../../journal/decisions/0213-keep-no-bookkeeping-a-committed-run-does-not-need.md))
and keep the whole deploy under the target (section 9).

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

Adopt steps 1, 2 and 5 of `docs/plans/derivation-invalidation.md`, with the
four changes phase 1 found (section 9, F1 to F4): if a re-run's output digest
equals the current generation's and the dependencies a release follows are
unchanged, record an equivalence and move no pointer; a recipe names the
digest of the import closure of the code that stage and kind actually runs,
plus a separate environment digest. These are prerequisites for everything
below and are the last whole-graph re-derivation.

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

Measured against the memory row in the phase 1 rehearsal: the one
`project/inventory` unit takes the cycle process from 0.87 GB to about
4.0 GB (4.3 GB with cutoff off, 4.06 GB on the parent code), and a build
takes about 6 GB, on the 8 GB worker. The rehearsal's other peaks (R5
2.07 GB, R7 3.92 GB) differ for a reason its receipt does not show; the
inventory alone reaches the higher one. Phase 1's post-check records the
cycle service's peak on the cycles that run the inventory or a build.

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
  generation when bytes match. Until phase 2 splits the import cycle, an edit
  inside it still re-runs every project and link unit (F4): CPU, not rows.
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
  the `[state-size]` measure against the 55 GB phase 4 trigger, which the 0a
  receipt shows already passed (open decision 3).

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
`[state-size]` measure against the phase 4 trigger. Its rollback
plan names the pressure drain that a return to 019 starts (phase 0
conditions). _Accept when_ a
deploy touching no parser re-queues zero parse units (receipt), a re-run with
identical output writes no generation, and a code-only deploy grows the
file by under 100 MB, per-unit bookkeeping included (settled 2026-09-26 under
D-0183 by trimming the bookkeeping, D-0213; R10 measured 84 MB).

_Phase 1 status: implemented offline 2026-09-25, rehearsed and review-fixed 2026-09-26, open items resolved 2026-09-27, closed 2026-09-27 after the eighth review round and a closing rehearsal on the final tree (R11); not deployed. Every acceptance check passes offline: a `cycle.py` edit queues 0 parses, identical re-runs write 0 project generations and 1 link generation (a real change), and the code-only drain grows the file 77.5 MB (84 MB by table). The first switch from a current state mints no project generation and keeps every link. The production canary (deploy step 7) is the acceptance of record._ Records:
[D-0205](../../journal/decisions/0205-cut-off-identical-derivation-output.md)
(cutoff),
[D-0206](../../journal/decisions/0206-add-migration-33-after-interning.md)
(migration 33),
[D-0207](../../journal/decisions/0207-scope-code-recipes-by-import-closure.md)
(scoped recipes),
[D-0208](../../journal/decisions/0208-keep-the-64-gb-cap-until-closure-collection.md)
(the 64 GB cap stays until invalidation step 3),
[D-0209](../../journal/decisions/0209-identity-history-records-changes-only.md)
(identity history records changes only),
[D-0210](../../journal/decisions/0210-pace-the-inventory-like-history.md)
(the inventory is paced like history),
[D-0211](../../journal/decisions/0211-a-derivation-that-raises-retries-on-a-clock.md)
(a derivation that raises retries on a clock),
[D-0212](../../journal/decisions/0212-cut-off-across-the-moved-column-and-held-parses.md)
(cutoff across the moved coverage column and for held parses),
[D-0213](../../journal/decisions/0213-keep-no-bookkeeping-a-committed-run-does-not-need.md)
(no bookkeeping a committed run does not need) and
[D-0214](../../journal/decisions/0214-a-derivation-backlog-does-not-pause-acquisition.md)
(a derivation backlog does not pause acquisition). The rules live in
[local state](../reference/state.md) (H15, H7, invalidation) and
[parsing](../reference/parsing.md) (admission, versions).

Four findings changed steps 1, 2 and 5 as written:

- **F1, closure-stable.** Cutoff on output alone publishes stale data. The
  release closure and the retention walk follow each generation's recorded
  dependencies, and history names every dancer generation. If dancer D
  becomes D' and history re-runs with identical output, a kept history still
  names D: the next closure publishes D's row, or withholds every link that
  names D'. So a unit cuts off only when its direct `derivation` entries and
  its `selected_*`, `source_observations` and `calendar_observations` groups
  equal the kept generation's. `build/` needs no change. Code-only and no-op
  re-runs cut off; a real upstream change still mints new ids downstream, as
  today, until phase 3's read sets.
- **F2, replay epochs.** A replay can overwrite live rows another stage
  enriched: on the 16:10 copy, inventory had changed `wsdc_status` on 4 of
  160 `wsdc` calendar rows. With the pointer kept, inventory would stay
  current and the enrichment would be lost. So `capture()` keeps a witness of
  six revisions (`canonical`, `dancers`, `links`, `source_event_map`,
  `source_events`, `observations`). A cutoff that moved any of them bumps a
  per-kind `replayed` token, which dependents' fingerprints and certificates
  include, so they re-run and cut off in turn with no new id. Calendar,
  dancer and source_index have no prerequisites and carry no token.
- **F3, pinning, not lone units.** A scheduled `source_event` unit nests its
  event's reuse, so "no cutoff in a group of more than one" would mint all
  469 source_event ids each deploy and, through F1, every history and link.
  Instead a member whose provisional id a sibling captured is pinned and
  never cuts off; siblings see the kept id.
- **F4, one import cycle.** A static walk from `state/db.py`,
  `project/process.py`, `link/service.py` or `build/service.py` reaches 191
  of 233 modules. After three import moves (the source registry split,
  `VERIFIED_MISS_SHA256` into `sources/records.py`, `PHASE1_KINDS` into
  `sources/phase_one.py`) a parse scope is 28 to 36 files (39 to 47 with the parse writers) and an extract
  scope 17 to 24. Project, link and build scopes stay at about 214 files, so
  an edit inside the cycle re-runs every project and link unit. Phase 2
  splits the cycle. `schedule/parse.py` enters parse scopes by its own bytes
  only; a test pins its imports.

Smaller choices made in the work:

- History owns `events.coverage_tier`. Map and calendar output no longer
  include it: history rewrites it later, so every map re-run differed from
  its own generation and minted new ids down to the links.
- Parse cutoff covers the five shadow index kinds, whose generations never
  chain: their kept generation is the newest undecided one whose fingerprint,
  or an equivalence of it, is the desired one.
- `recipe/runtime` stays in every stage's inputs. After the switch it is the
  absent digest and never changes, so bundles captured before it reproduce.
- No module is parse-only for a single kind: the admission modules and the
  parse writers are in all 17 parse scopes. Parse narrows per kind through
  its adapter.

Review fixes (2026-09-26), each with a test that fails without it:

- **Revocation follows equivalences.** Revoking a generation revokes every
  fingerprint cut off to it, in admission, parse cutoff and the four
  evidence readers (the build's included). Before, the same inputs parsed
  again were accepted by admission. The readers already refused any parse of
  a revoked snapshot under the same parser label. A cutoff keeps labels
  byte-equal, so the shared query (`revoked_evidence`) leaves out an
  equivalence with its generation's label and returns one row per revoked
  generation however many deploys re-parsed it; the readers' row budgets
  stay budgets of revoked generations. Tests pin the query, its budget and
  the build's use of it.
- **Parse writers are in every parse scope.** `admission/select.py`,
  `support.py`, `state/observations.py`, `verification.py`, `findings.py`,
  `history/acquisition.py`, `intent.py`, `origin_dispatch.py` and
  `fetch/wayback.py` write what a parse unit leaves behind; each enters every
  parse scope by its own bytes (parse scopes are now 39 to 47 files). A test
  pins every import of `schedule/parse.py` and of each writer that falls
  outside the scope. Shallow entries add no data files, so a migration
  re-parses nothing.
- **Every non-success has a clock (I3).** The parse retry fingerprint keeps
  `version/repository`, so a fix anywhere re-opens blocked parse attempts
  (a few units, not a re-parse). A derivation that raises retries after 15
  minutes, doubling to six hours, and is reported each time
  ([D-0211](../../journal/decisions/0211-a-derivation-that-raises-retries-on-a-clock.md));
  output that differs for identical inputs is `NondeterministicOutputError`,
  a failure, not a silent supersession.
- **Link history records changes only (D-0209).** A link re-run leaves an
  unchanged subject's rows alone, so no `identity_link_history` pair is
  appended. This was 208 MB of R5's 415 MB.
- **The inventory is paced (D-0210).** At most once per 12 hours; a due
  history lifts that only once it has consumed the inventory, so a dancer
  change mid-cascade cannot restart the cascade. At most two cascades a day,
  plus one per calendar replay or calendar generation the inventory has not
  read (see steady-state growth). A failing history is not due. Once an inventory
  has run since history came due and its cascade was interrupted too, the
  inventory runs whenever it is stale until history lands, so a dancer change
  in every cascade cannot starve history. The watchdog mirrors the gate and
  clocks a due history from the first inventory run since it came due, so
  starvation alarms. Both paced gates count a cutoff as a materialization, as
  does the watchdog's history clock. A held inventory (not current, made
  within its interval) and the map behind it do not close the phase-two gate
  or `accept-year`: they read the committed event list (I5), and the cycle
  counts that gate's refusals by reason. A current inventory holds nothing,
  so a pending map behind it blocks.
- **Operator controls.** `[derivation] cutoff = false` turns cutoff off at
  schema 33, a forward fix short of the byte-copy rollback. The cycle
  summary's `derivation` key counts generations, equivalences and replay
  bumps by kind; the watchdog adds `[replay-loop]` (an epoch moving three
  cycles running with no generation of that kind or a kind it reads, so
  unrelated dancer changes do not hide it), `[state-size]` (over 50 GB
  without `checkpoints/`, `venv/` and `uv-cache/`) and `[unit-failing]` (a
  queued unit whose last three attempts failed for the same reason; an I/O
  error, retried by design, once it has failed 20 times running or for over
  an hour). A
  report query that fails, including the replay-epoch read at the start of a
  cycle, is noted in the cycle record (`derivation_error`,
  `controls_error`) instead of losing it. The switch stops new cutoffs only; undoing a bad
  one takes a deploy that changes the affected code scope
  ([output cutoff](../guides/operation.md#output-cutoff)).
- **Candidate pruning fits the worker.** `gc --plan --candidates-only` and
  `gc --apply --candidates-only` list and remove superseded candidates
  without the graph walk the 8 GB worker cannot finish (the full plan peaked
  at 12.1 GB on a clone), and both plans keep any candidate a selected build
  generation names. An apply reports how many removable files are past
  their age floor (`eligibility`) and says on stderr when none is: a copied
  or restored state directory resets that floor. The watchdog adds
  `[equivalences]` (over 500,000 rows in `derivation_equivalences` or in
  `source_equivalences`, D-0208's trigger) and `[report-failing]` (a cycle
  report failed or `derivation` is missing).
- **Smaller.** A reuse through an equivalence replays that equivalence's
  continuity support and moves the replay epoch when it rewrote live rows;
  migration 33 also narrows the `source_selections` trigger; `recipe_inputs`
  fails closed when a unit's code scope is not accepted, and offline
  selection then skips that kind only and names it in the cycle summary's
  `readiness_errors`, which fails the cycle, and every other reader counts
  such a unit as pending; `gc --plan` reports
  equivalence counts and the collector rule (D-0206, invalidation step 3);
  the retention walk shares identical decoded children;
  `mise run evidence-size -- --from @-` measures one change of a stack.

Final review fixes (2026-09-27), each with a test that fails without it:

- **A repaired parse is parsed again.** A parse cut off to the accepted
  generation skipped admission, the only writer of `parse_status='ok'`, so a
  parser regression and its fix left every snapshot of the kind `failed`:
  registry checks turned unusable, coverage counted the unit withheld, and
  parse recovery kept picking it. The cutoff path marks the snapshot parsed
  before the registry re-reads it (D-0205).
- **Ordered JSON extracts parse fresh.** A stored extract is key-sorted, and
  the registry's and WDR's parse output follows the body's key order. A
  re-parse from the stored extract (any `recipe/env` or parser-only change)
  differed from the accepted generation for about 23% of registry and some
  WDR snapshots on the 16:10 copy, so it never cut off. Those kinds
  (`ORDERED_EXTRACT`) always extract from the body.
- **Stale-by-fingerprint work shows (I4).** A project or link code scope, or
  `recipe/env`, queues hints for the units it makes stale, and a replay epoch
  queues its readers' units, so the watchdog's `[stale-progress]` sees a
  derive that never picks them up. Derivation rows no longer count toward
  the 10,000-item acquisition mark
  ([D-0214](../../journal/decisions/0214-a-derivation-backlog-does-not-pause-acquisition.md)):
  a project-scope deploy queues about 32,900 units, and events wait up to 12
  hours behind the paced inventory.
- **`doctor` and `summary` fit the worker.** Both walked the retention plan,
  13 GB on the 35 GB copy; they now report the file's bytes and walk only
  with `--retention`. Summary peaked at 4.5 GB without it, 4.4 GB of that
  the publication report's closure check; its unit has `MemoryMax=5G` and
  `OOMScoreAdjust=800`. The rollback's check uses `PRAGMA user_version`.
- **Smaller.** A calendar replay the inventory has not read lifts its wait
  (F2; D-0210). A supersession whose inputs did not move retries on D-0211's
  clock. `recipe/env` digests the installed distributions and pyproject's
  `[project]` table, not the whole `pyproject.toml` and `uv.lock`, so a
  tooling edit re-parses nothing. The watchdog adds `[size-cap-near]` (the
  file over 90% of the retention cap), mirrors the calendar-replay rule, and
  a test pins `REPLAY_CAUSES` and `PACED_BY_INVENTORY` to the package. A
  parse-unit D-0213 test and the D-0213 and supersession tests join the core
  run. The core run's time over three minutes (maintenance item 9) is
  accepted debt for this switch: no phase 1 test is among its slowest, and
  the slow ones (the 33-page cohort, the crash boundaries) are behaviours
  the testing guide keeps in core.

Seventh review round (2026-09-27), each with a test that fails without it:

- **No hint outlives its unit's currency.** Only a unit run deleted its
  hint, so an A->B->A deploy (a revert before or after the drain) left about
  35,800 hints for current units, which the watchdog counted as ready work:
  a permanent false `[stale-progress]`. Offline selection now records the
  hints it finds current and deletes them after its read snapshot, unless
  queued again since; the cycle summary counts them
  (`current_hints_dropped`).
- **A new calendar generation lifts the inventory wait.** It rewrites the
  calendar's `events` rows as a replay does, dropping the inventory's
  enrichment (F2's other half). The epoch the inventory read is the highest
  over its generation and every equivalence, not the latest row's. The
  watchdog mirrors both, tested on the real schema.
- **Smaller.** A replay hint carries the run's clock, not the wall clock.
  `swingset-summary` has `OnFailure` and the watchdog a `[summary-failed]`
  line, so a digest killed at its 5 GB cap does not go dark (a
  bounded-memory closure check is maintenance item 10). The supersession
  test patches `_inputs_unchanged` instead of branching on transaction
  state.
- **The final tree re-checked.** Before this round, review re-ran three
  900 s switch cycles on a clone with the final tree (`src` 6b3c8f12):
  7,418 parses (7,482 before), 170 superseded and 3 `needs_review` source
  generations (unchanged), no derivation generation but the first cycle's
  build, peak memory 6.32, 4.03 and 4.20 GB, file growth 311 MB (307 MB
  before), `failed=true` only on the known scoringdance snapshot. Queuing
  the hints (31.7k project, 2.9k link) took the accept from 8.2 to 14.6 s
  and raised no new watchdog line (scratch receipt `rev-p1-ops8`, not
  retained).

Closing review (round 8, 2026-09-27) found no blocker or major. It re-checked
the seventh-round fixes (each has a test that kills its main mutant; 13 of 22
mutants killed in one review, survivors below) and I1, I3, I4 and I5 for
regressions. Conditions carried to the deploy:

- The preconditions in the deploy steps still apply: the publish tier's
  independent check with a written verdict, a byte copy before the switch,
  and the canary switch (step 7) within 48 hours of the post-check.
- Open decision 3 (what comes first as the 64 GB cap nears) is recorded
  before the go, as step 3's go/no-go requires.
- The minor findings below are in this plan and the operation guide before
  the deploy (done at close: the rollback's free-space check, the hint drop
  invariant and its fence bump in [local state](../reference/state.md),
  `current_hints_error` in [scheduling](../reference/scheduling.md)).

Carried forward (none stalls production or loses data):

- **Test gaps (surviving mutants).** Nothing pins that the D-0212 strip
  (`derivations._without`) removes only `events.coverage_tier`; stripping
  `first_seen_at` too survives (more conservative, not stale). Nothing pins
  that `pacing.calendar_replayed` takes the highest epoch (`read =
int(version)` survives); the watchdog's `calendar_made` exclusion of
  `wsdc-history`; the group path of a replay hint's run clock; the
  `current_hints_error` path; the `created_at > made` boundary in
  `calendar_regenerated` (`>=` is harmless). Earlier survivors stand:
  `mark_parsed_always`, `fair_parse_only`, `without_strip_all_tables`.
- **Watchdog.** `inventory_paced` needs an inventory hint, while
  `pacing.holds` holds any stale inventory; a stale inventory with no hint
  (made stale by an upstream generation that queued nothing) lets map, event
  and source-event hints count as ready, a false `[stale-progress]` for up
  to 12 hours. A failing history retries on D-0211's clock and shows only as
  `failed=true` until `[unit-failing]` names it after three same-reason
  failures (about 1.75 hours). A hint dropped for a unit that later goes
  stale without a new hint is found by `candidate_units`' fallback, so
  scheduling is whole, but the watchdog, which reads `pending_work`, cannot
  see it.
- **Revert cost.** An A->B->A deploy drops the current hints (29,463 on the
  first pass, 0 on the second), but the inventory, map, events, source
  events, history and links stay stale, since B's drain moved the replay
  epochs. The inventory B made is inside its 12-hour interval, so the
  cascade waits up to 12 hours with `[stale-progress] ... project units wait
for the paced inventory`. Expected, not a fault.
- **Hint drop.** `drop_current_hints` deletes by unchanged `enqueued_at` and
  bumps `history_dispatch_fence` once per row; after a revert one batch
  deletes about 35,800 and voids that cycle's earlier dispatch proofs. The
  post-check watches it.
- **I6 debt.** Equivalences, their recipes and root sets have no collector
  in this phase; each replay-epoch cascade adds about 35,000 equivalence
  rows. Only `[equivalences]` at 500,000 bounds them (D-0208).
- **Core time.** 1,304 tests in 196 to 255 s, over the three-minute rule
  (maintenance item 9).

Committing: phase 0's change is not pushed, so `mise run evidence-size`
against `trunk()` counts its evidence too (276 KiB against 200). Check phase
1's change alone with `mise run evidence-size -- --from @-` (50 KiB), and
phase 0's from its own parent.

Acceptance ran on 2026-09-26 on APFS clones of the 16:10 UTC copy on the Mac,
with no network: parent code (phase 0, `45d32250`) on one clone, the new code
on the other, since the Mac interpreter re-queues every parse on both. Drivers
call `accept`, `derive_one` and `build.closure.select` directly. One
[receipt](../../journal/evidence/runtime/architecture-phase1-rehearsal-2026-09/receipt.json):
the closing run (R11) at its top level, the 2026-09-26 runs (R0 to R8)
under `earlier_2026_09_26`. Production receipts are the evidence.

| Check                               | Expected                                                                                                                                             | Result                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| ----------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| R0, migration 33                    | Minutes, `foreign_key_check` included; 019 code refuses schema 33                                                                                    | 60 s on a warm cache, almost all of it the `foreign_key_check` (review: 176 s under load); no violations; the file grew 57 KB. Phase 0 code refuses: "database schema 33 is newer than supported 32".                                                                                                                                                                                                                                                                                                                    |
| R1, first accept                    | Both re-queue about 33,906 parse units                                                                                                               | Both enqueue 33,903 parse units (33,906 pending). The new code changes `recipe/env`, `recipe/runtime` and all 44 scope names.                                                                                                                                                                                                                                                                                                                                                                                            |
| R2, the 2026-09-21 replay           | Parent mints about one generation per unit and withholds links; new mints only where output differs, keeps every link                                | Parent: 35,801 generations for 35,801 units; every link withheld until the links re-ran. New: 6,339, once. The 29,077 dancers, 384 source indexes and one calendar cut off. The `wsdc` calendar differs only in `events.coverage_tier`, which history now owns, and by F1 inventory, map, all events, source events, history and links mint once too. Both withhold every link between the drains; after them both select all 2,933 (773 before).                                                                        |
| R3, parse sample                    | Source generations only where results differ; no project unit pending from an equivalence                                                            | 2,000 snapshots. Parent: 2,000 generations; 2,104 project and 240 link units stale. New: 1,976 equivalences, 24 generations, 0 units stale. Of the 24, 7 differ from the accepted result. The other 17 repeat a `needs_review` or `waiting_for_inputs` generation, which parse cutoff does not keep.                                                                                                                                                                                                                     |
| R4, code-only accepts               | `schedule/cycle.py` comment: project, link and build scopes only, 0 parses; `sources/eepro` comment: its extract and parse scopes and snapshots only | `cycle.py`: the 10 project, link and build scopes and `version/repository`; 0 parses. The same tree again: nothing. The eepro comment: its 3 extract and 3 parse scopes and `version/repository`; 560 eepro snapshots, no others.                                                                                                                                                                                                                                                                                        |
| R5, growth after a code-only accept | 0 generations; under 100 MB                                                                                                                          | **Generations met, growth not.** After the `cycle.py` accept, all 35,801 units re-ran: 0 project generations and 1 link generation, whose output really changed. The history anchor and all 2,933 links stayed selected throughout. The file grew 415 MB: link history about 208 MB of rows, and per-unit admissions, attempts, equivalences and root sets. History took 6 s.                                                                                                                                            |
| R7, R2 and R5 with the review fixes | R5 without identity history                                                                                                                          | Fresh clone, review-fix tree. R2: 6,340 generations, 1.74 GB, 9,068 s. The `cycle.py` accept changed the 10 project, link and build scopes and `version/repository`; 0 parses. The drain re-ran 35,801 units: 1 link generation (the same real change), 35,800 equivalences, all units current after, 8,476 s. The file grew 170 MB: derivation rows, equivalences and root sets 65 MB, identity history 0.05 MB (208 MB in R5), execution admissions and their dependencies 86 MB, work and requirement attempts 18 MB. |
| R8, final tree, project only        | The R7 numbers hold on the final review-fix tree                                                                                                     | Review's own clone, `src` 7a1f88c7: migration 0 violations (176 s under load); R1 33,903 parses; R2 project 3,406 generations; R5 0 generations, 146 MB (R7 145 MB); links on a 300-link sample 0 generations and 2.15 MB after the accept. R9 re-checked it on the final tree (below).                                                                                                                                                                                                                                  |
| R10, open items (D-0212, D-0213)    | First switch keeps every link; code-only growth under 100 MB                                                                                         | Clone B made current by the parent code, then switched: 0 project generations, 1 link generation (a real change), every link selected throughout; 95 MB. Clone A after its switch drains, the `cycle.py` accept: 0 project and 1 link generation, 84 MB (65 derivation rows, 0.05 identity history, 19 bookkeeping). Migration 59 to 83 s, 0 violations.                                                                                                                                                                 |
| R11, closing rehearsal, final tree  | R0 to R5 on the tree that ships                                                                                                                      | See below.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| Tests                               | Zero failures                                                                                                                                        | Final tree (2026-09-27): core suite 1,304 passed (196 to 255 s; over the three-minute rule, accepted debt, maintenance item 9); full suite 2,813 passed (749 s); `ruff` and `mypy` clean                                                                                                                                                                                                                                                                                                                                 |

R11, the closing rehearsal (2026-09-27; `src` of the working copy
`392165732a13`; three clones of the 16:10 copy, all deleted; same
[receipt](../../journal/evidence/runtime/architecture-phase1-rehearsal-2026-09/receipt.json),
top level):

- **R0.** Migration 33 with its `foreign_key_check`: 67 s (73 s on a
  second clone), 0 violations, the file 57 KB larger. Phase 0
  code then refuses: "database schema 33 is newer than supported 32".
- **R1.** Both codes queue 33,903 parse units. The new code also queues a
  hint for each of the 35,801 project and link units.
- **R2, the copy as it is.** Parent: 32,868 project and 2,933 link
  generations, 2.08 GB, every link withheld between the drains. New: the
  293 generations of the cascade in flight on the copy (245 events, 47
  source events, history), 32,575 equivalences, 314 MB; then 2,933 link
  generations and 605 MB, as R10's clone A. Both withhold every link
  between the drains and select all 2,933 after (773 before).
- **R2, from a current state.** The parent's drained clone, switched: 0
  project generations, 32,868 equivalences, 71 MB; links 1 generation (the
  real change), 2,932 equivalences, 58 history rows, 19 MB. The history
  anchor and all 2,933 selected links stay throughout; 2,932 keep their
  generation.
- **R3.** 2,000 snapshots. Parent: 2,000 source generations, 1,864 project
  and 240 link units stale. New: 1,992 equivalences, 8 generations (7
  `eepro.round` results that differ from the accepted one, then superseded,
  and one calendar), 0 units stale. The 17 held repeats of R3 now cut off
  (D-0212).
- **R4.** `cycle.py` comment: the 10 project, link and build scopes and
  `version/repository`, 0 parses (hints for the 35,801 units). The same
  tree again: nothing. Back to B after R5: the same 10 scopes, 0 parses,
  6,338 units stale (the revert cost). The eepro comment: its 3 extract and
  3 parse scopes and `version/repository`; 560 eepro snapshots, nothing
  else.
- **R5.** After the `cycle.py` accept, all 35,801 units re-ran: 0 project
  and 1 link generation (the same real change), 35,800 equivalences,
  replay bumps on calendar, inventory and map, 0 units stale and 0 hints
  left. The history anchor and all 2,933 links stayed selected. Growth
  77.5 MB by `page_count` (84 MB by `dbstat`: root sets 32, equivalences 31,
  work attempts 13, execution dependency index pages 5.5, identity history
  0.05). Peak 3.8 GB, 10,233 s on the Mac.

R8 in full: migration 0 violations, 176 s under load. R1: 33,903 parses. R2
project: 3,406 generations, 29,462 equivalences, one `project/map` replay
bump; 0 units not current. R5 after the `cycle.py` comment: 0 parses, 0
generations, 32,868 equivalences, 146 MB project growth (R7 145 MB); 0 not
current. R3 and R4 as above. Links, on a 300-link sample of a clone of the
16:10 copy: the switch drain minted the 300 and added 5,363
`identity_link_history` rows (73 MB); after the `cycle.py` accept they wrote
0 generations and 300 equivalences, 0 history rows, moved no link or event
replay epoch and grew 2.15 MB (about 21 MB for 2,933).

R9, the final tree. The fixes after R8 are not all outside `derive_one`:
`derivation_dependencies.replayed_kinds` puts the replay epoch of every
prerequisite kind into a unit's dependencies, which changes fingerprints for
inventory, map, event, source-event, history and link units, and the build's
event coverage reads `revoked_evidence`. So the fourth review re-ran R8 on a
clone of the 16:10 copy with `src` identical to the working copy at 18:50 UTC
(scratch `workflow/rev-p1-ops6/outb/*.json`; the clone was deleted). The
numbers hold exactly. Migration: 125 s under load, 0 foreign-key violations.
R1: 33,903 parses. R2 project: 3,406 generations, 29,462 equivalences, one
map bump, 1,135 MB, peak 3.89 GB. Links on the 300 sample: 300 generations,
5,363 history rows, 73 MB. After the `cycle.py` comment accept: 0 parses
queued; project 0 generations, 32,868 equivalences, bumps on calendar,
inventory and map, 145 MB, peak 4.03 GB; links 0 generations, 300
equivalences, 0 history rows, 2.15 MB; 0 project units not current. The
receipt's `after_this_recheck` note predates R9. The fifth fix round (the
starvation breaker, `pacing.holds`, one `revoked_evidence` row per revoked
generation, watchdog lines, `gc` eligibility and the candidate comparison
tool) is in scheduling, reporting, evidence readers and tools, and changes
no fingerprint.

R10, the open items (2026-09-27, decided by the operator under D-0183), each
with a test that fails without it
([receipt](../../journal/evidence/runtime/architecture-phase1-rehearsal-2026-09/open-items-receipt.json)):

- **The first switch cuts off (D-0212).** The cutoff comparison takes
  `events.coverage_tier` out of a calendar, map, event or dancer scope's kept
  rows. Clone B was first made current with the parent code (all 2,933 links
  selected), then switched: 0 project generations, 32,868 equivalences, and
  every link selected throughout, the same link ids and history anchor as
  before. The link drain wrote 1 generation (the real change R7 found), 2,932
  equivalences and 58 history rows. The switch drain grew the file 95 MB
  (76 project, 19 links); the parent's drain on the same clone grew 2.08 GB.
  Clone A, the copy as it is, caught a cascade in flight (map at 12:47, older
  history and 292 events and source events). Those 293 minted, differing from
  their kept generations only in `selected_project_map`, and through history
  all 2,933 links; the deploy's first drain finishes that cascade too.
- **Held parses cut off (D-0212).** An enforced unit keeps its newest held
  generation when admission would hold the repeat the same way.
- **Growth is under 100 MB (D-0213).** A committed unit run deletes its
  admission and dependency rows, and a success with no finding writes no
  requirement attempt. On clone A after the switch drains, the `cycle.py`
  comment accept (10 project, link and build scopes and
  `version/repository`; 0 parses) re-ran all 35,801 units: 0 project and 1
  link generation, 0 units not current after, and 84 MB of growth (65 project,
  19 links): 65 MB of derivation rows, equivalences and root sets, 0.05 MB of
  identity history, 13 MB of work attempts and 5.6 MB of page splits in the
  execution dependency indexes (no rows kept). R7 grew 170 MB.
- **The four majors of the last review** hold: the watchdog applies the
  inventory's wait only while no inventory has materialized since history
  came due, and `pacing.deferred` lets the inventory run once one has (the
  starvation breaker); `run_cycle` configures the pacing gate; the build's
  event-coverage reader uses `revoked_evidence`; and
  `journal/tools/releases/compare_candidates.py` is in deploy step 6. Each
  test was re-checked by removing its fix.

What the rehearsal found:

- **The first switch minted once (fixed, R10).** Moving `events.coverage_tier`
  to history changed the `wsdc` calendar's output, so the first drain wrote
  about 6,300 generations and withheld every link until the links re-ran.
  The comparison now leaves that column out of the kept rows (D-0212).
- **Growth (fixed, R10).** R5 grew 415 MB, 208 MB of it
  `identity_link_history` re-appended by link re-runs with identical output
  (D-0209). R7 then grew 170 MB: 65 MB of derivation rows, equivalences and
  root sets, and 104 MB of per-unit bookkeeping every unit run wrote (an
  execution admission with about nine dependency rows, a work attempt and a
  requirement attempt; about 2.9 KB a unit with indexes). A committed run now
  keeps only its work attempt (D-0213).
- **Parse cutoff kept only accepted generations (fixed).** A unit whose
  latest generation is `needs_review` or `waiting_for_inputs` minted an
  identical one on each re-parse (0.85% of the sample). It now keeps it when
  admission would hold the repeat the same way (D-0212).
- **Replay epochs make a revert cost CPU and time.** Accepting the unedited
  tree after R5 made 6,338 units stale, since calendar, inventory and map
  moved their epochs in R5. They cut off, but behind the paced inventory:
  up to 12 hours (carried forward, revert cost).
- **Listing ready links is slow.** Both codes take 26 to 29 minutes to list
  2,933 links, because `ready()` recomputes `all_dancers_current` (0.6 s) for
  each link outside a read-only snapshot. This is pre-existing. On the Mac, a
  full drain took 35 minutes for projects and about 2 hours for links.

Steady-state growth. Phase 1 removes per-deploy generations, not the growth
of ordinary work. Each dancer change re-mints the inventory and, by F1, the
map, every event and source event, history and every link. On the 16:10 copy,
19 dancer changes on 2026-09-25 gave 5 such cascades with identical output
(18 in two days): 12,138 event and 1,513 source-event generations, about
0.43 GB a cascade. Each build (2 to 3 a day) adds a 244 MB proof to the file
and a candidate directory of about 190 MB that nothing prunes. The worker
measured more than the copy: the 0a receipt (2026-09-26 21:35 UTC) shows a
46.6 GB file growing about 5 GB a day under 019's unpaced cascades (31,057
event generations since 2026-09-25), 63.6 GB of state without `checkpoints/`
(with `venv/` and `uv-cache/`, which `[state-size]` leaves out; their size is
unmeasured), and 121.3 GB of quota free. So `[state-size]` (50 GB) and the
55 GB phase 4 trigger have already passed. With the inventory gate (D-0210,
at most two cascades a day, plus one per calendar replay or calendar
generation) the copy suggests about 1.5 to 2 GB a day for the
file; that is an estimate, not a worker measurement, and the post-check
measures it. From about 48 GB after the switch (file at the switch plus about
1.2 GB, see cost) that reaches the 64 GB cap in about 8 to 11 days, before
phase 2 or invalidation step 3 exist; under 019 the cap comes about
2026-09-30. Local backups need free space of the state without checkpoints
plus 20 GB (about 84 GB at 0a, 37 GB of margin) and lose margin at two to
three times the daily growth, since the state, the checkpoint and the need
all grow. The order of what comes first is open decision 3. Removing
superseded candidates (`gc --plan --candidates-only`, then
`gc --apply --candidates-only` after an independent check; it walks no graph,
so it runs on the worker) frees about 5 GB once; `gc --collect-closures` frees
only proofs of failed builds, since a build generation or candidate names
every other proof. Phase 3's read sets remove the cascade and phase 4 the
bytes (D-0208).

Deploy steps (operator; one switch with 0b):

Preconditions: 0a deployed and post-checked; the
[invalidation plan's gate](derivation-invalidation.md#when-this-may-start) (a
clean day of the running candidate) or an explicit waiver; the publish tier's
independent check of the diff and the rehearsal, with a written verdict; a
current Mac byte copy.

1. Build the deploy tree from the 0b and phase 1 change; its `src/` must
   equal the reviewed change.
2. Hold for steps 2 to 4 only, with owner, reason and a review date 24
   hours out: write `operator-hold`, stop `swingset-cycle.timer` and
   `swingset-backup.timer`, and wait for any running unit
   ([holds](../guides/operation.md#holds)).
3. With every unit stopped, checkpoint the WAL as the service user:

   ```sh
   sudo -u swingset /var/lib/swingset/venv/bin/python -c "import sqlite3; print(sqlite3.connect('/var/lib/swingset/state.sqlite').execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone())"
   stat -c %s /var/lib/swingset/state.sqlite-wal
   ```

   The row must be `(0, N, N)`: 0 means not busy, and every frame was
   checkpointed. The WAL must then be 0 bytes (or absent). Otherwise a unit
   still holds the database: find it, stop it and repeat; never copy
   `state.sqlite` alone while its WAL has frames. Then byte-copy
   `state.sqlite` (46.6 GB at 0a, about 5 GB more a day until the switch) to
   the Mac through `/mnt/mac`, as the stability plan and 0a did, next to the
   weekly copies in `~/swingset-offload/`. On the Mac it takes no quota, so
   local backups keep running through the drain and the canary, and the
   rollback copies it back. Preflight receipt: the copy's sha256, schema
   32, file size, `derivation_generations` and `source_generations` counts
   and bytes, pending work by stage, the pending parse units whose last
   attempt was blocked (`parse_failed`, `needs_review`, `waiting_for_inputs`;
   9, 281 and 8 on the 16:10 copy), accepted inputs, `du` of `candidates/`,
   `venv/` and `uv-cache/`, the `[state-size]` measure, quota free, and the
   published candidate's id. Its rows are compared in step 6 with the tool
   below; keep the candidate directory (`gc` never removes the published
   one).

   Go/no-go, in the receipt: the file and the state's growth per day,
   measured from the 0a receipt to now; the days until the file reaches
   64 GB, from its size plus about 1.2 GB for the switch at the measured
   rate and at 1.5 to 2 GB a day; and the days until local backups are
   refused, from the margin (quota free minus `du` of the state without
   `checkpoints/` minus 20 GB) over two to three times the daily growth.
   Go only with open decision 3 recorded and at least 3 days to the cap after
   the switch. If the copy must stay on the worker (the Mac cannot take it),
   put it at `/var/lib/swingset-phase1-copy/state.sqlite`, outside the state
   directory. Then about 121 GB free at 0a less the copy leaves about 70 GB:
   `[disk-low]` (under 100 GB) fires at once, the one-copy room check
   (about 84 GB at 0a) refuses every backup until step 8, `[backup-stale]`
   fires 36 hours after the last one, and there is no backup from the switch
   until step 8. Keep `swingset-backup.timer` stopped then and say so in the
   receipt.

4. `nixos-rebuild switch`. A switch can restart stopped timers, so check
   `systemctl is-active swingset-backup.timer` after it and, if step 3 kept
   the copy on the worker, stop it again; record both in the receipt. Then
   end the hold: remove `operator-hold` and
   `systemctl start swingset-cycle.timer`. Start `swingset-backup.timer`
   unless step 3 kept the copy on the worker; then it waits for step 8. The
   marker blocks every cycle and backup, so none runs until this step. The
   first cycle is long: it migrates to 33 (1 to 5 minutes; R0 took 60 s on
   the Mac with a warm cache, 125 to 176 s in review under load, all on the
   35 GB copy; the worker has 8 GB of memory for a file of about 50 GB) and
   accepts the scoped
   recipes: every parse unit is re-queued once and every derivation is not
   current once. It will probably also build, since the last build is older
   than the 6-hour interval: on a clone that build ran from pre-switch
   generations, withheld links, added a 244 MB proof and a candidate useless
   for release, peaked at 6.3 GB, the highest of the drain on the 8 GB
   worker, and used most of the 900 s budget, so only 473 parses ran. The
   next cycle ran 3,177 parses at 4.2 GB and deferred the build. This build
   is accepted as the switch's cost; record its `MemoryPeak` in the watch
   receipt.
5. Watch the drain per `AGENTS.md`. It is watching, not a hold: the preflight
   receipt names its owner and a review date 24 hours out. The parse drain
   takes about 4 to 12 hours for 33,906 parses (review measured 730 and
   2,150 parses in two 900 s cycles; candidate 018 took about 7.5 hours);
   with production's 40-minute cycles that is about 6 to 17 cycles. The
   paced inventory can add up to 12 hours. Expected while the parse backlog is
   above the backpressure marks (1,000 items or 128 MiB): 0 requests a
   cycle, `[stale-fetch]` after two hours, and `[stale-progress]` on links
   until history runs. The project and link hints (about 35,800) do not hold
   fetching (D-0214). Expected from the first watchdog run and to stay:
   `[state-size]` (the measure is over 50 GB), and `[size-cap-near]` once
   the file passes 57.6 GB. Expected once step 3's margin runs out, or at
   once if the copy stayed on the worker: the room check refuses each
   backup, which fails the unit (`[backup-failed]`), and `[backup-stale]`
   fires 36 hours after the last one. `[disk-low]` fires when free
   space falls under 100 GB, at once if the copy stayed on the worker,
   within days otherwise. About 300 blocked parse units re-open because
   `version/repository` changed; the 9 that ended `parse_failed` (on the
   copy) fail again, and when few other parses of their source run in a
   cycle they trip the per-source 10% rule, so `failed=true` caused only by
   those snapshots is expected (the review saw it on a scoringdance page
   blocked since 2026-09-23). Any other `failed=true` is not. Check that
   fetching resumes once the parse queue is under the marks, not in the
   first cycles.
   After the first cycle, builds run at most once per 6 hours under
   backpressure, each a 244 MB
   proof and a candidate of about 190 MB. A successful build's proof stays
   named by its build generation and candidate; `gc --collect-closures`
   removes only a failed build's. Confirm the drain
   (0 pending project and link units, fetching resumed) or, at the review
   date, record why not.
6. Post-check receipt after the drain. Expected: no project or link
   generation for the switch itself (R10), only those of a cascade in flight
   at the switch (on the copy, 293 project and all 2,933 link generations;
   019 cascades every few hours, so expect one) and real output changes
   (1 link in R10), each listed with its cause; source generations for
   superseded older snapshots and held repeats that read live state (6% of
   3,070 in review's two cycles, fewer now held repeats cut off); about
   33,900 source and 35,800 derivation equivalences; one proof per build; and
   ordinary cascades, each about 3,400 project and 2,933 link generations,
   at most two a day plus one per calendar replay or calendar generation
   (D-0210); count the inventory runs a calendar lifted. List anything else. Also record pending
   project and link units (0 after the drain), `derivation_input_versions`
   rows with namespace `replayed`, the `pending_work` rows for project and
   link (`SELECT stage,count(*) FROM pending_work WHERE stage IN
('project','link') GROUP BY stage`) against the pending units, which must
   match (a gap is a lingering hint), the cycle summaries' `derivation`
   counts, `current_hints_dropped` (a large drop voids that cycle's
   dispatch proofs through `history_dispatch_fence`; note any) and any
   `current_hints_error`,
   `gc --plan --candidates-only`'s `equivalences`, file growth attributed as
   in step 7 (the switch's equivalences, root sets, recipes and bookkeeping
   apart from the in-flight cascade's generations, ordinary cascades and
   build proofs), parse units re-queued, history
   unit seconds; the migration's seconds from the first cycle's log; the
   cycle service's peak memory (`systemctl show -p MemoryPeak`) on cycles
   that ran the inventory or a build, against about 3.5 to 3.9 GB for a
   cycle with the inventory unit (both codes, on fresh clones) and about
   6 GB with a build, not the receipt's 2.23 GB project-drain peak;
   `swingset-summary.service`'s `MemoryPeak` against its 5 GB cap (4.5 GB on
   the copy);
   the `derivation_equivalences` and `source_equivalences` row counts
   (D-0208's collector trigger; `[equivalences]` fires when either passes
   500,000); the measured daily growth of the
   `[state-size]` measure and of the file, and the dates they reach 55 and
   64 GB; link numbers (link/event generations and equivalences,
   `identity_link_history` rows added, and `replay_bumps` for `link/event`,
   0 in steady state); plus 0b's
   `stages_disabled`, the first local backup's `checkpoint-verify` result
   and seconds, and the `[state-size]` measure against the 55 GB phase 4
   trigger. Compare the published candidate named in step 3 with the first
   candidate built after the drain (0 pending project and link units;
   builds during the drain withhold every link), as the service user:

   ```sh
   cd "$(systemctl show -p WorkingDirectory --value swingset-cycle.service)"
   sudo -u swingset env TMPDIR=/var/tmp /var/lib/swingset/venv/bin/python \
     -m journal.tools.releases.compare_candidates \
     /var/lib/swingset/candidates/<published> \
     /var/lib/swingset/candidates/<first-after-drain> /var/tmp/phase1-compare.json
   ```

   It runs from the deployed source tree, where `python -m` finds
   `journal/`, and spills to `/var/tmp`, not the small `/tmp`. It leaves out `run_id`, `first_seen_at`, `last_seen_at` and
   `asserted_at`, caps DuckDB at 1 GB (1.5 GB resident and 5 minutes for two
   of the copy's candidates), and lists per table the rows, the keys added,
   removed and changed, and which columns changed. Put the counts and
   changed columns in the receipt and explain every difference. Only real
   source changes, per-build columns (`evidence_cutoff`, `health_as_of` and
   the like) and the `coverage_tier` cascade's rows (R2: events' tier, now
   written by history, and the rows that carry it) may differ. The full `gc --plan` does not fit the worker; run it only
   on a Mac clone of a byte copy. The rehearsal compared counts and
   selections, not published rows.

7. A canary switch whose only change is a comment in `schedule/cycle.py`,
   held only for the switch as in steps 2 and 4. It re-runs every project
   and link unit and re-parses nothing; the drain takes up to about 24 hours,
   because the inventory and history each wait up to 12 (D-0210). Fetching,
   ordinary cascades and builds go on meanwhile, so whole-file growth says
   nothing about the canary. Its measure is the bytes it wrote: after the
   drain, on a Mac clone of a byte copy, the rows the canary's re-runs added
   (`derivation_equivalences` and the `derivation_dependency_sets` and
   `derivation_recipes` rows they name, `identity_link_history`, and
   `work_attempts`), counted by the runs since the canary's accept and
   priced at each table's bytes per row from `dbstat`. Receipt: 0 parse
   units re-queued by scopes (the blocked parse units re-open again, about
   300; their source generations are counted apart); 0 derivation
   generations for the canary, or the few with real output changes, listed;
   generations of ordinary cascades and each build's proof listed
   separately; the canary's bytes per table (pass: under 100 MB; R10 84 MB
   for 35,801 units). This is the phase's acceptance.
8. After the post-check and the canary pass, keep the Mac copy as that
   week's copy. If step 3 kept the copy on the worker, an independent check
   of its removal, then remove it with a receipt and start the backup
   timer.

Cost: one rebuild; migration 33 (1 to 5 minutes; the first cycle after the
switch is long); the last whole-graph
re-derivation, 4 to 9 hours of CPU: every
snapshot re-extracted and re-parsed once (about 33,906, since the extract
reuse key changes meaning; extracts are content-addressed), and every project
and link unit re-run, since every recipe changes. It writes
about 33,906 parse equivalences (about 35 MB) and 35,000 derivation
equivalences (10 to 15 MB); no generation for the `coverage_tier` move
(R11: 90 MB for the derivation drain from a current state, against 2.08 GB
for the parent code). Production runs 019 unpaced and will almost surely
switch with a cascade in flight, as the copy did in R10 and R11: expect
about 0.9 GB of derivation growth (R11: 314 MB for 293 project and 605 MB
for 2,933 link generations) plus the first cycle's build (a 244 MB proof); three real
cycles on the final tree in review added 307 MB, 244 MB of it the build. So
about 1.2 GB, which step 3's go/no-go uses. Add a byte copy of about 47 to
55 GB to the Mac and the canary: up to about 24 hours of drain behind the
paced inventory and history, and an operator watch for it. After it, a
code-only deploy costs CPU for what its scopes name (R11: 2.8 hours on the
Mac for a cycle-module edit, half of it listing links) and near-zero
generations, and 78 to 84 MB of rows (R11), about 19 MB of it bookkeeping.
The file does not shrink; the 64 GB cap stays (D-0208).

Rollback: the previous system generation is 0a (schema 32), which refuses
schema 33, so rollback is that generation plus the step 3 byte copy; work
since the switch is lost and re-fetched. SQLite replays any valid
write-ahead log beside the file, so a schema-33 `state.sqlite-wal` left next
to the restored copy corrupts it, and a rollback after a crash or a killed
unit is exactly when that log holds committed frames. The steps are in the
[operation guide](../guides/operation.md#rolling-back-a-migration): write
`operator-hold`; stop the cycle, backup, summary and watchdog timers and
units; switch to the 0a generation; move `state.sqlite`, `state.sqlite-wal`
and `state.sqlite-shm` aside; check free space for the copy (the moved-aside
file stays on the quota); copy the step 3 file back and check its sha256
against the preflight receipt; move any schema-33 `run_*` checkpoint out of
`checkpoints/`, so a 0a backup does not take it as the newest; then remove
the hold and start the timers. Code and
config roll back together: code older than phase 1 rejects phase 1's
`sources.toml` ("unknown scheduling settings", for
`inventory_interval_seconds`), so never run it with phase 1's config. 0b
ships in this switch, so returning to 0a turns event pressure back on:
`event_index` expansion stays closed until the dirty rows drain (about 5
hours per week the stage was off), busy hosts may stay latched, and the
operator watches `event_index` requests by host for a day.

Known limits:

- Parse cutoff keeps a held generation only when admission would hold the
  repeat the same way from its bytes and the contract review (D-0212). A hold
  that reads live state (manifest checks, retained WDR rows) still mints an
  identical generation on each re-parse.
- For an enforced kind, admission side effects (observation rewrite, watches,
  `interpreted` events) are not replayed for an identical parse result. An
  edit to a parse writer re-parses every kind, but a change in what it writes
  for an unchanged result needs a re-admit or a `PARSER_VERSION` bump
  (D-0205).
- Equivalences, their recipes and root sets are not collected until
  invalidation step 3, which has the rule (D-0206). A code-only deploy that
  touches no parser adds about 35,800 derivation equivalences and about
  39,600 `derivation_dependency_sets` rows (33,753 project and 5,866 link
  root sets, about 32 MB, the largest share of R10's 65 MB) and no source
  equivalences. A deploy that changes `recipe/env` or every parse scope also
  adds up to 33,900 source equivalences (about 35 MB). `[equivalences]`
  fires past 500,000 rows in either equivalence table: after about 13
  code-only deploys beyond the switch's 35,800 for derivations, and about 14
  environment or all-parse deploys for sources. It does not count root
  sets, which grow as fast; step 3's collector removes them with their
  equivalences. Past the trigger, step 3 moves ahead of phase 2 (D-0208). A committed unit run keeps only its
  `work_attempts` row (D-0213); failed runs keep their admission, and the
  1.9 GB of bookkeeping already on the copy (746,339 settled admissions with
  5.8 million dependency rows) stays. Phase 2's `work_queue` and phase 4's
  sweep own it.
- The paced inventory delays new events and dancer facts in events, history
  and links by up to 12 hours, and history by a further interval when a
  dancer change interrupts the cascade it waits on (D-0210).
- Published `first_seen_at`, `last_seen_at`, `asserted_at` and `run_id` come
  from the first derivation.
- A re-run whose output differs reads its rows twice inside its write
  transaction; in R2 the history unit took 60 s this way (54 s on the parent
  code), well within its 300 s allowance.
- Replay epochs are per kind: one dancer replay that flips a live
  `registry_placements.event_id` re-runs all 2,932 links (CPU; they cut off).
- `admission.support.selection_digest` includes `desired_fingerprint`, so any
  re-parse still forces a rebuild (pre-existing).
- A parse that ends `blocked` (`parse_failed`, or admission `needs_review` or
  `waiting_for_inputs`) has no retry clock; it re-opens only when
  `version/repository` changes, on a deploy (pre-existing, I3). Phase 2's
  `work_queue` gives it one (`waiting_input` and `quarantined` with
  `next_attempt_at`).
- A unit exception retries on a clock of 15 minutes to 6 hours, so a unit
  that fails every retry never makes three failing cycles in a row; the
  watchdog's `[unit-failing]` names it instead.

**Phase 2: split the cycle and fix the queue (about 2 weeks).** Five systemd
units; `acquisition_plan` written by `plan` and read by `acquire`;
`work_queue` with leases, backoff and quarantine, and `waiting_input` and
`quarantined` states with `next_attempt_at`, so a blocked parse gets a retry
clock (a phase 1 known limit); chunked generation writes;
per-loop progress counters and the reason-with-expiry table for guards; the
`bounded_write` alarm demoted to a safety net. The event-site review and the
history gate are deleted from the acquisition path in this phase. Breaking the
import cycle along the loop boundaries narrows the project, link and build
recipes (F4). _Accept when_ `kill -9` of any loop mid-write leaves the other
four progressing on the next tick; a unit that raises three times is quarantined with one alert
line and the queue keeps draining; `acquire` issues origin and Wayback
requests while `derive` holds blocked units; no `bounded_write` alarm fires
in a week.

**Phase 3: partition the globals and the release (about 3 weeks).**
`project/history/all` becomes `year/<yyyy>`; `project/map/all` becomes
per-source-index plus per-event resolution; link units record read sets with
a reverse index; `release` builds partition artifacts with per-partition
withholding, a small manifest, streamed key joins, the strictly-better gate,
and the `coverage` reduce. Read sets lift F1's limit: a real upstream change
then re-mints only the units that read it. Retain the old closure path behind
a flag for one release. _Accept when_ one corrupt event withholds only itself
in a build; a release build peaks under 2 GB resident and finishes under 10
minutes; a single fetched score sheet re-derives one event, its links and its
year, under one minute of derive time; the manifest is under 5 MB and identical across two
attempts with unchanged inputs; a release that would drop identity links is
refused with a reason.

**Phase 4: storage lifecycle and backups (about 2 weeks).** _Trigger:_ start
it, or raise the quota, when the state without `checkpoints/`, `venv/` and
`uv-cache/` passes about 55 GB; one local checkpoint then leaves little room
(D-0203). The watchdog's `[state-size]` uses that measure and fires 5 GB
earlier, at 50 GB; post-check receipts carry the size. Payloads, parse
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
