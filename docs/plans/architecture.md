# Swingset target architecture: independent loops, partitioned releases, bounded state

Adopted 2026-09-25 by the owner ("move the proposal to plans … pause current
work and implement this plan"). Other plans are paused until this one's phases
land, except operating the worker safely. Phases 0 and 1 are deployed and
phase 2's switch 2a runs since 2026-10-02 ([status](../status.md)). Phase 3
is implemented offline and not deployed; its rehearsal found four defects
(F26 to F29), fixed with the review's findings and rehearsed again
2026-09-30 to 10-02 (section 9); the build's 2 GB peak is not met on the
Mac. Phase 4 is implemented offline, reviewed in three rounds and
rehearsed on its final tree (closed 2026-10-03), not deployed; it goes out
with phase 3, and its file stays about 32 GB, not 15 GB, until open
decision 4. Phases 5 and 6 are not started.
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
   recorded before its go. Since then the cap went to 72 GB (D-0220) and
   76 GB (D-0233), and the owner put phase 4 before phase 3 (2026-10-02).
4. **Dependency records of labels nothing reaches** (phase 4, W1). After
   phase 4 the file is about 32 GB, not 15 GB: 16.1 GB is dependency sets
   that other sets name (build labels' `release_closure` members name the
   proofs; old inventory labels name the `input_row` sets). Freeing them
   means dropping the dependency record of labels no retention root
   reaches, which changes D-0119's permanent labels and D-0231's roots.
   Until the owner decides, every label keeps its record, and phase 4's
   post-check sets the cap from the file it measures.

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
processes when each write is short (I2). The process lock is not: it was
held exclusive for a process's life, so phase 2 made it a reader-writer lock
(F5, [D-0215](../../journal/decisions/0215-loops-share-the-state-under-a-reader-writer-lock.md)).
Two tables keep two writers: `watches` (acquire, and derive's parse
admission and link effects) and `cursors` (acquire, derive, and plan's
enumeration cursors); each write is short and SQLite serializes them.

| Loop         | Cadence                   | Owns (writes)                                                                           | Reads                                   | Progress counter                              | SLO / alarm                                                                                  |
| ------------ | ------------------------- | --------------------------------------------------------------------------------------- | --------------------------------------- | --------------------------------------------- | -------------------------------------------------------------------------------------------- |
| **acquire**  | every 5 min, 4 min budget | `watches`, `snapshots`, `hosts`, `host_budget`, spacing, `archive_captures`, blob store | `acquisition_plan`, operator pauses     | requests issued per host; snapshots stored    | a host with due work and no `Paused` reason issues ≥1 request per hour; `Paused` past expiry |
| **plan**     | every 30 min              | `acquisition_plan` (which watches and archive pages are eligible, with a reason)        | canonical tables, findings, acceptances | plan rows refreshed                           | plan age < 2 h                                                                               |
| **derive**   | every 5 min, 4 min budget | `work_queue`, generations, payloads, canonical tables, links                            | snapshots, blobs, overrides             | generations written; queue depth by state     | queue oldest ready unit < 6 h; quarantine count change → alert                               |
| **release**  | hourly; builds every 6 h  | partition artifacts, candidates, `PUBLISHED`, baseline                                  | generations, overrides                  | partitions built/withheld; commits            | a candidate per 24 h; withheld partitions listed; publication strictly better or refused     |
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
Phase 2 keeps today's scopes; the loops stop with their timers, and
loop-scoped pauses wait for phase 5, when the controls are simplified.

Built in WP7: the tick frame (`loops/runtime.py`), `derive` and `maintain`, and
their CLI commands; the cycle and derive share `schedule/offline.py`
([scheduling: loops](../reference/scheduling.md#loops)). A publication's fence
also refuses `runs`, `loop_state` and `guard_reasons` writes, so a tick that
finds one writes only its record file and the `loop:<l> publication_in_flight`
guard cannot be stored while the fence stands. Phase 3 (WP10) built the
narrow fence, which fixes both for partitioned publications.

Built in WP8: `plan` and `acquire` and their CLI commands
([D-0218](../../journal/decisions/0218-the-loops-judge-the-year-gate-on-facts-and-acquire-from-a-plan-table.md)).
`phase_two_allowed` and `history/acquisition.py` stay, since they are in every
parse scope (F12); inside the loops the year gate reads facts only
(`facts_only_year_gate()` in `project/history.py`), so the pending-projection
walk no longer runs in acquisition. Plan's reasons are the candidate's and the
year gate's (`year_unaccepted`, `acceptance_digest_changed`,
`year_findings_open`, `h7_h10_not_deployed`), not `awaits_index`. The cycle,
plan and acquire share the acquisition pass and the event bookkeeping stages
(`schedule/acquisition.py`, beside `schedule/offline.py`: the cycle imports
it, so it cannot live in `loops/`, which recipes exclude). Plan still runs
the `event_*` stages that `[stages]` enables (open decision 2).

Built in WP9: `release` and its CLI command, `release --dry-run|--publish`
([scheduling: loops](../reference/scheduling.md#loops)). It ticks hourly, not
every 6 h: `build_interval_seconds` paces builds (F16). It reads live tables
through the build as the cycle did. Each build holds `heavy.lock` exclusive,
so derive starts no unit meanwhile (F10).

Built in phase 3: `release` reads committed generations by pointer. The
default `partitioned` path selects current pointers, builds partition
artifacts from their generations and streams the assembly. Only the review
queue (open findings), the snapshot rows the artifacts' support names and the
correction token's policies and revocations are read live. Every build
holds the heavy lock while `[release] streamed_lock` is true (the default)
until the worker measures a partitioned build under 2 GB beside derive.
`whole` and `closure` (the phase 2 path) stay for one release
([D-0224](../../journal/decisions/0224-releases-from-partition-artifacts.md)). It skips while the disk quota has less than
`release_min_free_bytes` (30 GB) free, with a `loop:release` guard.

Built in WP11: `services.swingset.loops.enable` (default false) swaps the
cycle unit for the five loop services and timers, each with its own timeout
and memory cap; every unit's `uv sync` runs under `flock` (F11)
([deployment: loops](../guides/deployment.md#switching-to-the-loops)). With
it on, the watchdog runs `--loops 1`: `[loop-quiet]`, `[loop-failing]` and
`[plan-stale]` replace the cycle checks, and `[replay-loop]` reads derive
ticks. `[wal-large]` (frames not yet reset, since the file never shrinks) and
`[building-orphans]` run in both modes. The module evaluates both ways; the
switch is the operator's.

## 4. Data model for scale

### 4.1 Partitions

Every derived fact belongs to exactly one partition, and the partition key is
the unit key:

| Partition          | Contents                                                                                   | Built in phase 3                                                                                          |
| ------------------ | ------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------- |
| `event/<event_id>` | contests, rounds, entries, heats, callback marks, placements, judges, links for that event | release partition `event/<E>` (joined `E1+E2` when a row names both); units `project/event`, `link/event` |
| `dancer/<wsdc_id>` | one registry dancer row and their registry placements                                      | release partition `dancers/<id // 512>`; unit `project/dancer`                                            |
| `year/<yyyy>`      | registry association, event tiers and coverage rows of the year                            | unit `project/year/<yyyy>`; release partition `year/<Y>`. The occurrence inventory stays global (F18)     |
| `source/<s>`       | one source's listings and its mapping proposals; one resolution per event                  | units `project/mapping/<source>` and `project/resolution/<event_id>` (F19)                                |

The two global units are what make local defects global. `project/history/all`
does two things (`project/history.py` `finalize_history`): set
`registry_placements.event_id` by a series-and-month join, and rebuild the
`coverage` table. The first is a per-year fact: a year partition reads the
registry placements and events of its own year and writes their association.
The second splits by event year too. `project/map/all` becomes per-source
mapping plus a tiny per-event resolution, because an event's mapping depends
only on the listings that name it. An alias edit (`series_aliases.csv`)
queues the inventory and every year; only those whose output changes mint,
not the graph.

Built in phase 3 ([D-0222](../../journal/decisions/0222-derive-history-per-year-and-the-event-map-per-source-and-event.md)):
the year keeps the live `coverage` table, which the history review reads, so
the year owns its coverage rows and the release takes them from `year/<Y>`
rather than reducing at build time (F17). Index scopes are pages, so the map
splits per source (F19). The inventory stays one paced global unit (F18).

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

Built in phase 3 for link units
([D-0223](../../journal/decisions/0223-link-units-depend-on-what-they-read.md)):
the read keys are the event, the dancers used, content digests and a
per-event pool version that the dancer unit's probe bumps (F20), stored in
`derivation_reads` with its reverse index. The wrapper is a SQLite
authorizer over every project and link unit body; it counts undeclared
reads, fails in tests only, and misses `USING` joins in production (F25).
Project units keep their declared dependencies. A weekly verify sample
re-derives 1% of links and years.

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

**Provenance stays intact and gets cheaper.** A published release stays
reproducible from its inputs because the HF commit names the manifest
digest, the manifest names each fragment, each fragment names its
generations, and generations name snapshots by hash in the blob store.
D-0022's rule (a rejected legacy event row never vetoes a supported event;
revoked or unsupported rows withhold their event) holds per partition.
Immutability moves from row-level triggers on 130 tables to
"content-addressed files are never rewritten." `closure.hydrate` becomes
"read the manifest by digest."

Built in phase 3
([D-0224](../../journal/decisions/0224-releases-from-partition-artifacts.md),
[D-0225](../../journal/decisions/0225-publish-only-strictly-better-releases.md)):
all four steps. Artifacts live under `state/partitions/`, not yet in a blob
store (phase 4); the maintain loop collects unnamed ones. The manifest names
no generation ids itself; the sidecars do. The gate has 16 measures, and a
correction-only candidate is exempt.

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
Built in WP3 as an overlay (F14,
[D-0216](../../journal/decisions/0216-the-work-queue-leases-backs-off-and-quarantines.md)):
`pending_work` and derivation currency still say what needs doing, and a
`work_queue` row exists only for a leased or failing unit
([state: work queue](../reference/state.md#work-queue)). A blocked parse
waits for input on a 24 h to 7 day clock instead of quarantining. Recovery
stays: each loop tick closes its own dead runs, and a lease taken over
finishes the dead attempt. A unit whose process died in its attempt (a kill,
an OOM, a stop timeout) fails as `process_interrupted`, and one whose
admission raised fails on its queue row alone, so both back off and
quarantine like any failure. Folding the other tables in is phase 5.

**Bounded writes by construction.** A generation is written in chunks of
about 5,000 rows, each its own short transaction into content-addressed
payload storage under a `building` generation id; the final commit flips the
scope pointer in one statement (`derivations.complete` already separates
retention from the pointer flip). A crash leaves unreferenced payloads for the
collector (I6, I8). The `bounded_write` alarm stays as a safety net at 60 s
and its firing becomes an alarm, because under I2 it should never fire.
Built in phase 2 (WP4): a project or link unit's output is read back from its
live rows, so the live write commits first with a `derivation_building` row,
then the chunks, then one short transaction flips the pointer and finishes the
attempt. `project/history` and units whose kept generation exceeds 5,000 rows
stage, `accept-year`'s history included; the rest keep one transaction
([state](../reference/state.md#derivation-generations-h15)). A unit with a
building row is not current; its next successful run deletes building rows
of other inputs. Partial generations stay (they are immutable), and no
closure or retention window reads them.
Built in WP5: every write has the one 60 s alarm, history's 300 s allowance
is gone, and run records count firings for the watchdog's `[write-deadline]`
([D-0217](../../journal/decisions/0217-large-generations-store-in-chunks-and-the-write-deadline-is-an-alarm.md)).

**Guards fail closed with a clock.** Every branch that returns `Paused`,
`Wait` or `skipped` in `fetch/politeness.py` and `fetch/client.py` writes a
row `(host_or_watch, reason, since, expires_at)`; the watchdog alarms when
`now > expires_at` and the reason is still current, and the daily summary
lists reasons by count. "Legacy spacing unknown" would have alarmed in an
hour, not eight days. Phase 0 alarms on that state and on `Blocked` host
pauses from existing tables; the record itself needs a table and a package
change, so it lands in phase 2.
Built in WP6: the client counts guards by host, watch or loop, each pass
writes them to `guard_reasons` with an expiry per reason, and the watchdog
prints `[guard-expired]` and a daily `[guards]` count
([fetch guards](../reference/fetching.md#guards),
[D-0219](../../journal/decisions/0219-every-guard-that-holds-a-fetch-has-a-reason-with-an-expiry.md)).
"Still current" reads as seen after the expiry. The scheduler's own
pre-filter (paused or spent hosts, operator pauses, backpressure) records no
guard; those reach the table only when a skip reaches the client.

**Idempotent effects.** Publication already uses expected-parent commits
(`publish/service.py`). Backups become idempotent by naming segments by
content digest (section 6). Fetches are idempotent by the reservation model
already in `politeness.acquire`.

**Backpressure.** Each loop has one knob: `acquire` stops issuing when
`snapshots` without a parse generation exceed a byte budget; `derive` stops
leasing `project` units when the release loop is more than two intervals
behind; `release` refuses to start when free disk is under the scratch it
needs. All three are counters the watchdog can read. Phase 2 built the
first (counting only parse work derive can run now, D-0216) and the third
(`release_min_free_bytes`). Phase 3 built derive's knob
(`release_lag_intervals`, guard `loop:derive release_lag`), which engages
only when release's ticks were skipped because derive held the heavy lock,
and narrowed the publication fence to what a partitioned publication
checks, so acquire, plan and derive tick through it; a build still holds
`heavy.lock` (a partitioned one while `streamed_lock` is true) and derive
starts no unit meanwhile.

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

Phase 4 built this table as object classes under `objects/` with one
streaming collector ([state: objects and packs](../reference/state.md#objects-and-packs)).
Two rows differ: a superseded generation's output becomes a pack kept for
every label, not a mark-sweep victim, and dependency sets stay in SQLite,
where the named ones keep the file near 32 GB (section 9, phase 4, W1).

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
Phase 4 sizes a set's check by the database's bytes in use, not its file,
and collects the archive: segments no kept set names go and the history is
squashed so their bytes leave it (D-0234). The archive's budget is 40 GB
(three sets are about 15 GB), watched by `[archive-size]`.

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
  generation when bytes match. An edit to the shared storage, projection or
  build modules still re-runs every project and link unit (F4): CPU, not
  rows. Scheduler, loop and acquisition edits re-run nothing (WP12).
- **Versioned interpreters.** `EXTRACT_VERSION` and `PARSER_VERSION` labels
  (`versions.versions`) are replaced by the code digest per page kind;
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
  today, until phase 3's read sets. _Dropped in phase 3, WP7 (F21,
  [D-0224](../../journal/decisions/0224-releases-from-partition-artifacts.md)):
  releases read current pointers, and the closure path withholds a year that
  reaches a superseded generation._
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
  splits the cycle (WP12, below). `schedule/parse.py` enters parse scopes by its own bytes
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

7. A canary switch whose only change is a comment in `schedule/cycle.py`
   (in a tree with phase 2's WP12, `versions.py`; the cycle is then in no
   scope), held only for the switch as in steps 2 and 4. It re-runs every project
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
  code), well within its 300 s allowance. Phase 2 removed the allowance
  (D-0217): the rows are stored in chunks after the live write.
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
  watchdog's `[unit-failing]` named it. Phase 2 (WP3, D-0216) quarantines it
  at the third failure of one class instead and retires `[unit-failing]` for
  `[unit-quarantined]`.

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

_Phase 2 status: implemented offline 2026-09-27 (work packages WP1 to
WP13), rehearsed the same day (WP14), review-fixed in two rounds (2026-09-27
and 2026-09-28), closed 2026-09-29 after a third review with no blocker or
major and a closing rehearsal of the final tree, R0 to R10
([receipt](../../journal/evidence/runtime/architecture-phase2-rehearsal-2026-09/receipt.json));
not deployed. The cycle stays the service until
`services.swingset.loops.enable` is set. The first three acceptance checks
pass offline (WP10) and on the clones; the week without an alarm is
production's. One item stays open before switch 2b (the stop marker, below).
The history gate is not deleted: its module is in every parse scope (F12),
so in the loops it reads facts only (F13). The event-site review stays off
by config (phase 0)._
Records:
[D-0215](../../journal/decisions/0215-loops-share-the-state-under-a-reader-writer-lock.md)
(the reader-writer lock, table writers, publication as a pause, the heavy
lock),
[D-0216](../../journal/decisions/0216-the-work-queue-leases-backs-off-and-quarantines.md)
(the work queue; narrows D-0211 and D-0214),
[D-0217](../../journal/decisions/0217-large-generations-store-in-chunks-and-the-write-deadline-is-an-alarm.md)
(staged generations, the 60 s alarm; replaces D-0198's history allowance),
[D-0218](../../journal/decisions/0218-the-loops-judge-the-year-gate-on-facts-and-acquire-from-a-plan-table.md)
(facts-only year gate, the plan table; acceptance stays an acquisition input,
open decision 1) and
[D-0219](../../journal/decisions/0219-every-guard-that-holds-a-fetch-has-a-reason-with-an-expiry.md)
(guard reasons with expiries). Migration 34 adds `runs.loop`, `work_queue`,
`acquisition_plan`, `guard_reasons`, `loop_state` and `derivation_building`
([schema history](../reference/schema-history.md)).

Twelve findings changed the phase as written:

- **F5, one process lock.** `open_database` held `state.lock` exclusive for
  the life of the process, so five processes could not coexist;
  `control.lock` is a short admission mutex, not a writer lock. Loops now
  hold `state.lock` shared plus their own `loop-<name>.lock`; every
  exclusive holder (cycle, CLI mutations, backup, restore, `gc`,
  `accept-year`) first takes `state.intent`, so a waiting writer is not
  starved and new ticks skip while it waits (WP2).
- **F6, publication fences stop every write.** The fence aborts any write
  to a non-control table while a publication is active. With loops, a
  publish would fail every derive unit and fetch write. An active
  publication is a pause for the other loops, checked before a unit or
  request starts. A unit write that still meets it writes nothing more (the
  queue and attempt tables are fenced too); the next tick after the
  publication closes the attempt as `publication_fenced`, retried in 60 s
  and never counted. A tick whose start meets it skips.
- **F7, the write bound included the lock wait.** `bounded_write` started
  before `BEGIN IMMEDIATE`, and the default busy timeout was 5 s. Now
  `BEGIN IMMEDIATE` waits up to 70 s on every writable connection, the
  publication boundary's included, counted, and only the body is bounded;
  admissions wait 90 s for `control.lock`. A `ControlTimeout` or a busy
  timeout skips the unit or request and counts nothing.
- **F8, generations are read back from live rows.** A project or link unit
  writes live tables, then stores its generation from them, so the live
  write commits first. A commits live rows and a `derivation_building` row;
  pass 1 labels the output from a read snapshot; B stores refs in chunks of
  5,000; C flips the pointer and finishes the attempt. Between A and C the
  unit is not current, so no dependent runs. Generation ids are
  deterministic, so a killed B resumes by ordinal (WP4). The building row
  keeps the first capture's witness and a sticky "live rows moved" flag, so
  a resumed cutoff still moves the replay epoch (review fix).
- **F9, blocked parses closed acquisition.** Backpressure counted every
  parse row in `pending_work`, blocked ones included. It now counts only
  parse units derive could run now (WP3).
- **F10, memory.** A build peaks near 6.3 GB and the inventory unit near
  4 GB on the 8 GB worker. While release builds it holds `heavy.lock`
  exclusive (WP7, WP9). R3 showed parse-only derive ticks beside a build at
  2.1 to 2.9 GB (8.2 to 9.2 GB with the build) and up to 3.9 GB elsewhere,
  and the per-unit `MemoryMax` caps sum to 17 GB, so they bound no total.
  Derive now starts no unit while release builds (review fix), and switch
  2b is gated on the worker's measured peaks (step 8).
- **F11, `uv sync` races.** Every unit syncs one venv in `ExecStartPre`;
  it now runs under `flock` (WP11).
- **F12, 58 files enter every parse or extract scope.** Editing one
  re-parses every snapshot (33,900; 4 to 9 hours). They include
  `history/acquisition.py`, `history/origin_dispatch.py`,
  `history/intent.py`, `fetch/wayback.py`, `fetch/archive.py`,
  `schedule/parse.py`, `state/{findings,observations,verification}.py`,
  `admission/*`, `model/*`, `sources/*`, `cutoff.py` and five package
  `__init__.py` files (so `__version__` is never bumped). No work package
  edits them; a scope guard checked every package (0 parse or extract
  scopes changed).
- **F13, one derivation predicate in acquisition.** The year gate read
  facts except `pending_inventory_projection`. In the loops the gate is
  facts only (`facts_only_year_gate()` in `project/history.py`, outside the
  parse scopes); `plan` runs the offers pass and writes `acquisition_plan`,
  and `acquire` re-checks its target from facts (WP8). The cycle and
  `accept-year` keep the pending question.
- **F14, the queue is an overlay.** `pending_work` and derivation currency
  still say what needs doing; `work_queue` holds leases, clocks, waiting and
  quarantine, one row per leased or failing unit, deleted on success.
  Folding `pending_work`, `work_generations` and the requirement tables into
  it is phase 5. This keeps one definition of unfinished work, shared by
  the cycle and the loops, and a small migration (seeding reads the latest
  attempts of pending units only).
- **F15, quarantine storms.** A linker bug could quarantine up to 2,933
  units at once. The watchdog prints one line per quarantined unit up to 5
  per run, else one line by error class.
- **F16, cadence.** A 6 h release timer behind a 6 h build interval builds
  every 12 h. Release ticks hourly; `build_interval_seconds` paces builds.

Deferred, each with its phase:

- Loop-scoped operator pauses: phase 5. Today's `all`, host, source and
  kind pauses still apply; a loop stops with its timer.
- Narrowing the publication fence: built in phase 3 (WP10) for partitioned
  candidates. A closure-mode publication still pauses the other loops (F6),
  and a tick that finds one writes only its record file.
- Derive's release-lag knob (section 5): built in phase 3 (WP10). The heavy
  lock still covers whole and closure builds.
- Removing the cycle: phase 5. Until then both orchestrations share
  `schedule/offline.py` and `schedule/acquisition.py`, and both are tested.

Built in WP10: `tests/test_loop_isolation.py` (core) is the offline
acceptance of the first three checks. A subprocess (`tests/helpers/loop_fault.py`)
SIGKILLs each loop inside a write, derive twice (a single-path unit, and
between two chunks of a staged history generation), and acquire also during
a request, after its admission committed. The other four ticks progress; the
killed loop's next tick fails its dead run, leaves no running attempt, lease
or active admission (with a run or without one), and resumes the building
generation after the dead unit's backoff. An exclusive open waits for a
running derive tick and holds the next acquire tick (`skipped-overlap`). The
review added a unit whose admission raises, a unit that SIGKILLs its process
three times (both quarantined while the others drain) and a publication
that fences a unit mid-write (never counted). The week without an alarm is
production's check.

Built in WP12 (F4): `versions()` and `repository_identity()` moved to
`versions.py`; the digest still covers the package directory, so an
unchanged tree keeps its identity. `publish/safety.py` had imported them
from `schedule/cycle.py`, and that one edge put the scheduler, acquisition
and the loops' shared code in every project, link and build scope. Those
scopes went from 222 files (216 at phase 1) to 168, and the build scope to
174; `schedule/cycle.py`, `loops/`, `fetch/client.py`,
`history/backfill.py` and `schedule/fairness.py` are in none, and 43 such
paths joined `EXCLUDED` with reasons. Cutting the lazy `state/derivations.py`
imports of `build.closure` and `publish.safety` was measured and dropped: it
removes no file, since `model/schema.py` reaches `build/` and
`state/findings.py` reaches `publish/` (both F12 files). Parse and extract
scopes are unchanged. The first accept of this tree re-runs every project
and link unit once; cutoff keeps their ids.

Rehearsal, on APFS clones of the 16:10 UTC copy on the Mac with no network.
WP14 ran on 2026-09-27 on the tree before both review rounds; the closing
rehearsal ran R0 to R10 again on the final tree on 2026-09-29, and the
[receipt](../../journal/evidence/runtime/architecture-phase2-rehearsal-2026-09/receipt.json)
holds the closing numbers with a summary of the first run and of the
review's own short run. Each clone first accepts the parent tree (phase 1,
`0a53bd4d`), since the Mac interpreter re-queues every parse on a first
accept (I9), then the phase 2 tree. Harness processes stand in for the
timers; a mock transport answers 304 for known URLs and 404 otherwise.
Timings are lower bounds for the worker.

| Check                       | Expected                                                                                                                                                   | Closing result (final tree)                                                                                                                                              |
| --------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| R0, migration 34            | Minutes at most; 0 `foreign_key_check` violations; growth under 1 MB; `waiting_input` about the blocked parse count; phase 1 code refuses schema 34        | Met: 63 to 75 s, 0 violations, 340 kB, 301 `waiting_input`; refused                                                                                                      |
| R1, accept                  | Only the 10 project, link and build scopes and `version/repository` change; 0 parses queued                                                                | Met: those 11 inputs in under 6 s; 0 parses; 35,801 project and link hints                                                                                               |
| R2, cycle mode              | Parent and new cycle both fail only on the known scoringdance snapshot; new summary keys present; requests within 10%; `write_deadline_fired` 0            | Met on parity: both unfailed, 12 new keys and none lost, 0 firings, writes up to 13.2 s; request counts (13 and 93) not comparable (finding 4)                           |
| R3, five loops for 2 to 3 h | No tick fails except known parse failures; acquire requests throughout, builds included; 0 lock and control timeouts; each loop's longest write under 30 s | Met: 152 min, 0 timeouts and firings, one derive tick failed on the known snapshot, requests in every 10 min and during the build; longest write 12.9 s (build complete) |
| R4, `kill -9` of each loop  | The other loops' next ticks progress; the killed loop recovers its run, leases and attempts; `quick_check` ok                                              | Met: seven kills, derive's in its claim, admission and output write; each recovered; `quick_check` ok, nothing left open                                                 |
| R5, staged history          | Label equals the kept generation's; A under 30 s; each chunk under 5 s; a resume stores no duplicate ordinal; no building row left                         | Met: equal label on an unchanged re-run, A 2.1 s, 41 chunks up to 2.8 s, C 12 to 14 s; a kill in chunk 2 resumed whole                                                   |
| R6, poison unit             | Quarantined at the third failure; one `[unit-quarantined]` line; the queue keeps draining                                                                  | Met: quarantined at attempt 3, one line, 20 units per tick drained                                                                                                       |
| R7, blocked derive          | With 1,500 parse units `waiting_input` at production backpressure marks, acquire issues requests                                                           | Met: 20,991 parse units waiting, backpressure off, 29 requests (6 Wayback)                                                                                               |
| R8, watchdog in loops mode  | Only expected lines, each explained                                                                                                                        | Met: `[stale-progress]` (the copy's link backlog), then R6's `[loop-failing]` and `[unit-quarantined]`                                                                   |
| R9, module evaluation       | Off: cycle units only. On: five services and timers, no cycle, `flock` in every `ExecStartPre`, watchdog `--loops 1`                                       | Met                                                                                                                                                                      |
| R10, suites                 | Core, full, ruff, mypy and the scope guard: zero failures                                                                                                  | Met: core 1,537; full 3,044 plus 3 tests that pinned the pre-D-0220 config, fixed and re-run; ruff, mypy, scope guard 0                                                  |

Rehearsal findings. From the first run (2026-09-27):

1. The release generation completes in one write: 35.8 s beside a second
   build then, 12.9 s alone and 13.2 s in a cycle at close. Settled as a
   gate: step 5 measures it on the worker, and 2b waits for it to be under
   30 s or for the write to be chunked as WP4 chunks history.
2. `accept-year` stored history on the single path in one 61 s write.
   Fixed: the command stages history (A, chunks, C), as derive does.
3. Request admissions carried no run, so a killed acquire tick left its
   request admission active. Fixed: the admission carries the tick's run,
   and acquire's recovery settles it (at close no admission was left).
4. An acquisition pass ends when no watch is selectable but the next is
   due now (delay 0), in both trees: cycle requests ranged 13 to 111 from
   one copy, and 15 of 35 acquire ticks ended before 200 of 240 s at close.
   Open; sleep briefly instead. It makes the 25 % request gate noisy.
5. A derive tick with a failing unit fails, so a quarantine also raises
   `[loop-failing]`; each retry of the known scoringdance snapshots fails
   one tick.

From the closing run:

6. Memory. Cycle H's `peak_rss_bytes` was 6.66 GB; release's 7G cap is
   1.13 times that, under step 5's 1.15. If the worker reads the same,
   raise release's `MemoryMax` to 7.5G before 2b. The release loop peaked
   at 6.1 GB (1.23). Derive peaked at 4.6 GB, 86 % of its 5G cap, on ticks
   mixing project and parse units, without the inventory.
7. Step C of the staged history takes 8 to 14 s on the final tree (5.8 s
   in the first run): under the 30 s target. Step 4 records the worker's.
8. A killed build counts toward the build interval (D-0199 counts admitted
   attempts). In production a build killed at release's cap delays the next
   by 6 h.

Review fixes (2026-09-27, offline; rehearsed at close):

- A staged completion kept its replay bump only when A and C ran in one
  process: a retry after a killed A captured the moved revisions and cut
  off without telling dependents. The building row now keeps the first
  witness and a sticky flag (`derivations.building_moved`), and
  `complete()` applies the same rule.
- An exception in derive's admission (capture, lease, attempt) failed the
  whole tick and left nothing on a clock, so one defective unit stopped
  derive. It is now the unit's `unit_exception` failure on its queue row,
  and selection honours that row (`queue.holds`).
- A unit that kills its process (an OOM at derive's 5 GB cap, a stop
  timeout) was retried every tick and never quarantined. Its death is a
  `process_interrupted` failure, counted by the loop's recovery or a
  takeover; the offline loop rotates the kind group at admission.
- The fenced path wrote the fenced queue tables and raised. A fenced or
  busy write now writes nothing and is closed uncounted by the next tick;
  a fenced run start skips the tick.
- The publication connection had the default 5 s busy timeout. It has the
  70 s one, and its uncertain settle retries once.
- Orphaned building rows had no collector. A unit with a building row is
  not current, its next run deletes the others' rows, and partial
  generations are kept out of closures, retention windows and the
  retention plan's local and archivable lists (`incomplete`).
- The watchdog counted finished ticks, not progress. `[loop-idle]` reads
  `loop_state.last_progress_at`; release ticks that only waited for the
  build interval no longer break `[loop-failing]`'s streak, so repeated
  build failures alert; `[loop-quiet]` waits for a running backup; plan and
  release waits leave `loop:<name>` guards with expiries.
- Smaller: a fetch whose control admission times out skips instead of
  failing the tick; maintain prunes every loop record after 30 days and
  deletes queue rows with nothing left to do; derive names at most 20 held
  units, and only under a pause; release treats a disk reading older than
  an hour as unknown; records carry `peak_rss_bytes`.

Second review fixes (2026-09-28, offline; rehearsed at close), each with a
test that fails without it:

- A partial staged generation whose building row was collected was reused
  as whole when a unit's inputs returned to it: the pointer selected 3 of 4
  refs and the unit was current. Step A now gives the new building row the
  recorded label, the single path finishes it inline, and step C counts a
  reused generation's references too.
- A process that died inside derive's admission (capture, lease, attempt)
  left nothing, so the unit took every tick. The lease and the kind-group
  rotation now commit first; a dead run's lease with no running attempt
  is the unit's `process_interrupted` failure (recovery or takeover).
- One acquisition choice that raised ended the acquire tick. Each choice is
  contained: contention skips the request; any other error holds the
  watch (`request_error`, 1 h doubling, 7 d from the third), which the
  pass's flush keeps and a served request clears (D-0219).
- Smaller: an exception that escapes a unit is its counted
  `unit_exception`; a tick killed after SIGTERM leaves `runs/<run>.stop`
  and counts nothing; a recovery record that rolled back is no ending;
  derive progress counts only committed output; plan contains each
  bookkeeping stage and `projection_pending`; a fetch compares with the
  watch row as it is after the request, so a parse reset is not undone;
  maintain compacts loop `runs` rows after 30 days and counts partial
  generations (`[incomplete-generations]`); `venv.lock` is a lock, never
  state; the cycle summary carries `peak_rss_bytes`.

Closing review (round 3, 2026-09-28) found no blocker or major. It re-read
and re-tested the round 1 and 2 fixes (partial-generation reuse, death inside
the admission, escaped exceptions, fenced or busy writes left uncounted) and
killed 20 of 24 mutants of the round 2 fixes; core 1,537 passed, full 3,047,
ruff and mypy clean, scope guard 0. It also ran a short R3 and R4 on the
final tree (clone `diag-p2-r3A`, deleted). Conditions carried to the
switches:

- The publish tier's independent check, with a written verdict, before 2a
  (release publishes).
- Step 2's byte copy verified by sha256 and `quick_check` before 2a: the
  first 04:00 backup after 2a prunes the schema-33 checkpoints, so the copy
  is the only way back to phase 1.
- Step 5's gates met before 2b (the build's `complete` write under 30 s on
  the worker, release's `MemoryMax` at least 1.15 times the cycle's
  `peak_rss_bytes`, requests per hour per host within 25 %), and step 8's
  (release plus derive under about 7 GB) after it.
- Every switch while the loops are on, the 2b to 2a rollback included,
  writes `operator-hold` and waits until no `swingset-*` unit is active.
- Before 2b: the stop-marker timeout case below is fixed or accepted by the
  owner.
- A short R3 and R4 on the final tree before 2a: done, by the reviewer and
  by the closing rehearsal.

Open before switch 2b:

- **Stop marker versus start timeout.** systemd's `TimeoutStartSec` expiry
  (derive: 30 min) sends SIGTERM, which writes `runs/<run>.stop` as a
  deploy stop does, then SIGKILL 120 s later. A unit that overruns outside
  a bounded write (staged pass 1, the B loop, a reader snapshot) dies
  uncounted, retries in 60 s (`fenced_retry`) and never quarantines: derive
  alternates a 32-minute tick on it with a normal one, and no alarm fires
  (`[loop-failing]` needs three failed ticks in a row; `[loop-idle]` sees
  progress). Round 1 counted a stop timeout; round 2's marker undid that.
  Not reachable on today's data (derive ticks at most 290 s in R3). Fix:
  write the marker only for a stop the operator asked for (a hold or a
  deploy), or count a marked death whose tick outlived its start timeout.

Carried forward (none loses data; each is rare or costs throughput):

- **A failed step C.** Live rows depend on their own history. If C fails
  after A committed and the inputs later return to the pointer's exact
  fingerprint, the retry reuses the pointer's generation, but the abandoned
  A moved `coverage.last_changed_at`, so the compare fails as
  `nondeterministic_output`; history quarantines after three and links wait
  until a history input changes (scratch probe
  `rr3-corr/t/test_orphan_anchor.py`). Separately, a generation B stored in
  full whose C failed is whole once `collect_orphans` deletes its building
  row, and `build/closure.select` picks history anchors and link generations
  newest by `created_at`, not by pointer: a later cutoff to an older pointer
  can let the release closure take it. History's `last_changed_at` masks
  this; a link unit over 5,000 rows could hit it. No test covers it.
- **Cycle to loops.** Loop recovery settles only runs whose `runs.loop` is
  that loop. A cycle killed before 2b leaves an unfinished run, active
  request and operation admissions nothing settles in loops mode, and
  cycle-owned leases that hold their units `lease_busy` for up to 30
  minutes. Step 7 lets the cycle finish; never stop it mid-run.
- **Containment gaps.** Derive's `contained()` catches only
  `WriteDeadlineExceeded`, so a fence or busy timeout in the tick-start
  `queue.wake` or `parse_hint_recovery` fails the tick instead of skipping
  it. `discover()`, `discover_registry()`, `offers()` and `_pass`'s
  `prepare()` and `select()` are outside the per-choice containment: a
  defect or bad row there fails every acquire tick (I1); `[loop-failing]`
  alerts after 15 minutes. If `queue.fail` itself raises a non-sqlite error
  in `_admission_failed`, the lease stays with the finished run and the unit
  retries every tick with no clock (needs a second defect, in the queue).
- **Quarantine after admission deaths.** A `died_unclaimed` row has no
  input fingerprint, so such a unit waits out the seven days even after its
  inputs change ([local state](../reference/state.md#work-queue)).
- **Watchdog.** `[loop-idle]` for acquire is silent while any `host:*` or
  `loop:acquire` guard was seen in the last hour, so one guarded host hides
  an acquire stall on the others (the D-0190 pattern); `[guard-expired]`
  covers only the guarded host.
- **Heavy lock with a hub.** With a hub configured, `correcting` is true
  whenever any unit is unfinished, so every hourly release tick takes
  `heavy.lock` exclusive and blocks derive's unit starts even when
  `correction_needed()` is false. Throughput only; production runs dry-run.
- **Derive's 5G cap.** Nothing gates it against the inventory's peak (about
  4 GB on the Mac, unmeasured beside other work). On the worker three OOM
  kills quarantine `project/inventory` for seven days and new events stop
  until an operator requeues; `[unit-quarantined]` alerts. Measure it in
  2a's drain.
- **Test gaps (surviving mutants).** The `queue.lease` takeover branch to
  `died_unclaimed`; the CLI SIGTERM handler writing `runs/<run>.stop`; the
  fetch's commit-time compare (`moved = False` survives); the cycle's
  `failed` from `request_failures(summary)`.
- **Smaller.** `_admission_failed`'s docstring still says the admission
  rolled back its lease; core takes 284 s (467 s under load), over the
  three-minute rule (maintenance item 9).

Deploy steps (operator; two switches, 2a then 2b):

Preconditions: phase 1 deployed, post-checked and its canary passed; open
decision 3 recorded; the gates of steps 5 and 8 met before 2b; the
publish tier's independent check of the diff and the rehearsal, with a
written verdict (release publishes); a current Mac byte copy, verified by
sha256 and `quick_check`; before 2b, the stop-marker item above fixed or
accepted by the owner.

Switch 2a, the package in cycle mode (`loops.enable = false`):

1. Build the deploy tree; its `src/` equals the reviewed change. Run the
   scope guard against it with the phase 1 deployed tree as baseline: 0
   parse or extract scopes changed.
2. Hold (owner, reason, review date 24 h out); stop the cycle and backup
   timers; wait for running units; checkpoint the WAL to `(0, N, N)` with
   an empty WAL, as phase 1 step 3; byte-copy `state.sqlite` to the Mac.
   Preflight receipt: sha256 and `quick_check` of the worker file and of the
   Mac copy (the first 04:00 backup after 2a prunes every schema-33
   checkpoint, so the copy is the only way back), schema 33, size, pending
   by stage, blocked parse units, units whose last three attempts failed
   alike, accepted inputs, quota free, published candidate id.
3. `nixos-rebuild switch`; end the hold; start the timers. The first cycle
   migrates to 34 (63 to 75 s on the Mac, plus a `foreign_key_check` of
   about a minute if the operator runs one) and accepts the 10 project, link and build
   scopes and `version/repository`: 0 parses. About 300 blocked parse units
   become `waiting_input` on a 24 h clock.
4. Watch the drain per `AGENTS.md`: all 35,800 project and link units re-run
   and cut off. Under D-0227 and D-0228 history waits up to 48 h and the
   inventory up to 7 days; the 2b gates need only one cycle that built
   (builds every 24 h, D-0220). The tree keeps D-0221's parse thresholds
   (D-0230). History takes the staged path: record its A and C
   seconds and chunk count (2.1 s, 12 to 14 s and 41 on the Mac). Record
   the inventory tick's `peak_rss_bytes` against derive's 5G cap.
5. Post-check 2a receipt: 0 parse units queued by scopes; derivation
   generations only for cascades and real changes (listed); growth
   attributed (about 80 MB); queue by state, each quarantine explained;
   guards by reason; `write_deadline_fired` 0 and `write_seconds_max`; the
   candidate comparison as phase 1 step 6. Three numbers gate 2b:
   - the build's `complete` write: `write_seconds_max` of a cycle that
     built. Over 30 s, 2b waits until that write is chunked (finding 1);
   - the cycle's `peak_rss_bytes` (resident memory, in its summary) over
     cycles that built: release's `MemoryMax` (7G) must be at least 1.15
     times the largest, else raise the cap before 2b (the Mac read
     6.66 GB, which would need 7.5G, finding 6). systemd's
     `MemoryPeak` counts page cache of the 35 to 46 GB database file and
     reads near the machine's RAM whatever the process uses; record it as
     information only;
   - requests per hour by host over 24 h against the week before 2a: each
     host with at least 20 requests a day within 25 %. A shortfall is
     explained or rolls 2a back (R2 could not compare, finding 4).

Switch 2b, the loops (`loops.enable = true`; `src/` unchanged, so no recipe
changes):

6. Build the tree; the `src/` diff against 2a is empty; only `nix/` changes.
7. Short hold; stop `swingset-cycle.timer`; wait for the running cycle to
   finish on its own (a killed cycle leaves a run, admissions and leases
   the loops do not settle);
   `nixos-rebuild switch` (the cycle units go, the five loop units and timers
   come); end the hold.
8. Watch for 24 h: requests per hour by host against the 2a week (fetching
   goes on during builds and drains); every loop ticks; no `[loop-*]`,
   `[write-deadline]`, `[lease-stuck]` or `[building-orphans]` line;
   `peak_rss_bytes` per loop from the tick records (`MemoryPeak` as
   information only, it includes page cache); acquire's over ticks that
   fetched real 200 bodies, since its 1G cap was sized on R3's bodiless
   mock and is unverified until then; release builds while derive starts
   no unit (`heavy_deferred`); the 04:00 backup completes and the loops
   resume after it; WAL size. Gate: release's `peak_rss_bytes` plus that
   of the largest derive tick that overlapped a build with parse work
   queued stays under about 7 GB, else roll back to 2a.
9. Post-check 2b receipt: ticks and failures per loop, requests per hour,
   derive units per hour, plan age, quarantine count, guards by reason,
   write and wait maxima, memory peaks, WAL maximum. Optional, on the
   operator's call: `systemctl kill -s KILL swingset-derive.service` during a
   tick, then confirm the other loops' next ticks and derive's recovery.
10. Acceptance of record: seven days after 2b with no `[write-deadline]`
    line and no loop stalled.

Rollback: 2b to 2a is `loops.enable = false` and a switch, done as step 7:
write `operator-hold`, wait until no `swingset-*` unit is active, switch,
remove the hold. Without the hold systemd stops derive mid-unit and kills
it after 120 s; the stop marker keeps that death uncounted, but a hold
means no unit is cut off at all. Any later switch with the loops on
follows the same steps. The cycle takes the exclusive lock and releases
every lease, and a building generation resumes on its unit's next run. 2a to phase 1 needs step 2's byte copy
(phase 1 code refuses schema 34), following
[rolling back a migration](../guides/operation.md#rolling-back-a-migration);
work since the switch is lost and re-fetched.

Cost: 2a is one rebuild, migration 34, one re-run of every project and link
unit (35,801 on the copy; CPU of a code-only deploy: phase 1's R11 took
2.8 h on the Mac; the drain waits up to about 24 h behind the paced
inventory and history), about 80 MB of equivalences, root sets and
bookkeeping plus 340 kB for migration 34, 0 parses, about 300 blocked parse
retries, a byte copy of about 50 to 65 GB, and the independent check. If
the worker's cycle peak matches the Mac's, raising release's cap to 7.5G is
a one-line `nix/` change in 2b. 2b is minutes, no re-queue, and a day of
watching. After it fetching
no longer stops for builds or drains, and a failing unit costs three
attempts, not every cycle.

Known limits after phase 2:

- One SQLite writer for five processes. A long single write (the inventory,
  the build's proof row, a code-scope `accept`) blocks the others for its
  length; each loop's run record carries `write_seconds_max`,
  `write_wait_seconds_max` and `write_lock_timeouts`, which feed the
  PostgreSQL trigger (6.4).
- Loops skip while an exclusive command waits or runs: the daily backup
  pauses fetching for its length, and so does a publication (F6).
- The loops' year gate may fetch sheets for an accepted year whose event
  list a pending projection is about to change; the digest check closes the
  year once the list commits (D-0218).
- Blocked parses retry daily to weekly instead of never; a quarantined unit
  waits seven days unless its input changes or an operator requeues it
  (D-0216).
- The build's `complete` write is one transaction (finding 1): the 2a gate
  decides whether it must be chunked before 2b.
- About 600 run rows a day instead of 96; `maintain` prunes idle record
  files after 14 days and every loop record after 30, and compacts the
  rows' summaries to a stub after 30 days; the stub rows stay. The
  watchdog still globs `runs/`.
- Partial generations whose building row was collected (inputs changed
  between B and C) keep their refs and payloads until phase 4 collects
  them; maintain counts them and `[incomplete-generations]` fires above
  three.
- Derive waits whole while release builds; a long build delays parse work
  and can raise backpressure.
- A build killed mid-run (release's cap, a stop) counts toward the build
  interval, so the next build waits the full 6 h (D-0199).
- The watchdog reads with `immutable=1` and lags one checkpoint; more
  writers mean more `[watchdog-blind]` retries.
- Two orchestrations until phase 5 removes the cycle. In cycle mode (2a,
  or after a rollback) a unit that kills the process is retried every
  cycle, uncounted, as before phase 2; only the loops count deaths.

**Phase 3: partition the globals and the release (about 3 weeks).**
`project/history/all` becomes `year/<yyyy>`; `project/map/all` becomes
per-source-index plus per-event resolution; link units record read sets with
a reverse index; `release` builds partition artifacts with per-partition
withholding, a small manifest, streamed key joins, the strictly-better gate,
and the `coverage` reduce. Read sets lift F1's limit: a real upstream change
then re-mints only the units that read it. Release reading partition
artifacts lets the publication fence narrow to its own tables, and gives
derive its release-lag knob (both deferred from phase 2). Retain the old closure path behind
a flag for one release. _Accept when_ one corrupt event withholds only itself
in a build; a release build peaks under 2 GB resident and finishes under 10
minutes; a single fetched score sheet re-derives one event, its links and its
year, under one minute of derive time; the manifest is under 5 MB and identical across two
attempts with unchanged inputs; a release that would drop identity links is
refused with a reason.

_Phase 3 status: implemented offline (closed 2026-10-02), not deployed.
Work packages WP0 to WP13 landed 2026-09-29 and were rehearsed on
real-data clones (WP14, first table below). The four defects (F26 to F29)
and both reviews' findings are fixed; the final tree was rehearsed again
from a fresh clone on 2026-10-02 (closing rehearsal), R5, R9 and R10
included. Every accept-when holds offline except the build's peak: 2.43 GB
on the Mac (2.2 to 2.5 GB across runs), so the heavy lock and release's 7G
cap stay until the worker measures it (step 5). The cold build took 474 s.
Phase 2's switches come first; the deploy preconditions and the carried
review findings are listed under the deploy steps._ Records:
[D-0222](../../journal/decisions/0222-derive-history-per-year-and-the-event-map-per-source-and-event.md)
(years, mappings and resolutions; history pacing and the history lift
retired),
[D-0223](../../journal/decisions/0223-link-units-depend-on-what-they-read.md)
(link read sets; replaces the dancer cohort),
[D-0224](../../journal/decisions/0224-releases-from-partition-artifacts.md)
(releases from partition artifacts selected by pointer; F1 dropped;
`[release] path`; the narrow fence; D-0022 per partition) and
[D-0225](../../journal/decisions/0225-publish-only-strictly-better-releases.md)
(the strictly-better gate). Migration 35 adds `derivation_reads`,
`source_event_proposals`, `release_partitions`, `release_artifacts` and
`release_manifests`, and deletes the retired units' queue rows
([schema history](../reference/schema-history.md)).

Nine findings changed the phase as written:

- **F17, history is three per-year jobs.** `finalize_history` set
  `registry_placements.event_id` by series and month (same month, so same
  year) and rebuilt tiers and `coverage` grouped by event year: all three
  split exactly. The live `coverage` table stays, since the history review
  reads `events_accepted` from it. `project/year/<yyyy>` owns the
  association of placements reported in the year (at or after
  `history_start`), its events' `coverage_tier` and its `coverage` rows; the
  release takes them from `year/<Y>` partitions.
- **F18, the inventory stays global.** It matches legacy calendar editions
  across a December to January boundary and reads every non-registry event.
  It stays one paced unit (D-0210, without the history lift); F21 ends its
  cascade instead. Splitting it is a later plan item.
- **F19, "per source index" means per source.** Index scopes are pages: 846
  scope rows for 464 listings, so one listing sits on several.
  `project/mapping/<source>` (two today) proposes a target per listing into
  `source_event_proposals`; `project/resolution/<event_id>` (about 765)
  writes the event's `source_event_map` rows and its placeholder row. A
  resolution reads its proposals as content and only waits for the mappings
  (`waits_for`): depending on a mapping's generation would re-mint every
  resolution of a source, and a dependency on its own map rows moved during
  its own run.
- **F20, the linker reads the whole registry.** Candidate pools go by
  surname initial, name rarity counts every dancer with the same surname
  word, judge pools filter eligibility. A read set of "rows returned" would
  miss a dancer who becomes a candidate. A link's read set names its event,
  the dancers it used and content keys; pool effects are a per-event
  `link_pool` version the dancer unit's probe bumps, widening when unsure.
- **F21, F1 goes with the graph walk.** F1 existed because the release
  closure and retention followed a kept generation's dependencies. Releases
  now take current pointers, so a re-run whose output equals the kept
  generation cuts off on output alone (F2's replay epochs stay). A dancer
  change ends at the first unchanged unit.
- **F22, the closure path cannot anchor on history.** It anchors on every
  year, then every source event and dancer, and skips an anchor whose walk
  reaches a project generation other than that scope's anchor: those events
  are withheld (`year_anchor_conflict`), never published stale. It may
  withhold more than before; it stays for one release behind `[release]
path`.
- **F23, owned columns never enter their owner's inputs.** A year and a
  dancer's reconcile both write `registry_placements.event_id`; a year that
  read it would never be current. A dancer's reconcile that moves it bumps
  the dancer replay epoch, which years read.
- **F24, a release cutoff from inputs.** The manifest's `evidence_cutoff`
  is the newest `created_at` among the generations and accepted source
  generations it names, never the clock, so two attempts write the same
  bytes. Support checks read accepted source generations, not a clock.
- **F25, the authorizer's blind spot.** SQLite's authorizer never reports a
  table joined by `USING` or `NATURAL` whose other columns go unread. Armed
  mode (tests) adds those tables from the statement's plan; production would
  need a trace callback that would clobber a test's own, so it can miss
  them. No fixture has such a read; the weekly verify sample is the net.

What each package built:

- **WP0 to WP2.** A scope guard (0 parse or extract scopes changed at every
  package), migration 35 with three table modules, and the read recorder
  ([state: read recorder](../reference/state.md#read-recorder)): an
  authorizer over each project and link unit body, checked against `RULES`,
  counted as `undeclared_reads`, fatal only in tests. The fixtures run armed
  with zero undeclared reads for every kind.
- **WP3, years** ([state](../reference/state.md#year-units)). Years are not
  paced; the inventory keeps its interval without the history lift. The
  history review pack runs the inventory and every year through
  `process_unit`. A year run twice with no input change stays current.
- **WP4, mapping and resolution**
  ([state](../reference/state.md#mapping-per-source-resolution-per-event-plan-f19)).
  Neither re-projects events or reconciles placements globally; an event
  depends on its resolution. On a real-data clone the new units reproduce
  the old map with zero differences. A stale `project/map/all` hint from
  parse-scope code becomes every source's mapping unit.
- **WP5, link read sets**
  ([state](../reference/state.md#link-read-sets-plan-f20)). Two keys beyond
  the spec: `content:event_row` (a calendar, the inventory or a resolution
  owns the events row) and a row digest for a dancer no unit materialized.
  Readiness checks the event, resolution, years (dropped in review, D-0226)
  and read dancers, never the
  registry. On a clone of the 09-29 copy: readiness of all 2,934 links in
  2.7 s (it took 26 to 29 minutes); link runs no slower; about 1.7 keys per
  subject, so about 325,000 `derivation_reads` rows and 75 MB with the index
  (projected from 20 events). A soundness property test fails when the
  probe, the rarity step or the name step is disabled.
- **WP6, partition artifacts**
  ([build](../reference/build.md#partition-artifacts)). Selection reads
  pointers. An event partition takes the calendar, inventory and year rows
  it merges by payload hash, so a year re-mint rebuilds only partitions whose
  rows changed. The baseline enters the digest only of partitions with
  legacy rows. On an undrained clone: 2,991 partitions cold in 176 s at
  334 MB, 67 MB of artifacts; a second build rebuilt 0 in 32 s.
- **WP7, partitioned releases**
  ([build](../reference/build.md#partitioned-releases)). Every release runs
  stage 1 and writes the manifest; `path` is `partitioned`, `whole` or
  `closure`. The correction token's admission part is the artifacts' support
  digest with the live policies and revocations, so publication walks no
  graph. The reuse trigger leaves the baseline commit out, so an
  acknowledged baseline stays quiet. Only `build/manifest.py` and
  `state/release_store.py` join the project and link scopes.
- **WP8, streamed assembly**
  ([build](../reference/build.md#streamed-assembly)). DuckDB pass 1 over key
  columns, then one partition at a time through the whole path's revocation,
  identity, suppression and annotation functions; coverage from SQL
  aggregates, the changelog from a per-table join. Rows equal `whole`'s on
  every fixture. Beyond the spec, `whole` runs the same key check, so both
  close joins to a dancer the release lacks; the closure path does not. A
  synthetic release of 200,000 placements and 400,000 marks peaked at about
  530 MB on the Mac.
- **WP9, the gate** ([publishing](../reference/publishing.md#publication-gate)).
  The verdict is in every candidate as `_meta/verdict.json`; `publish`
  refuses before any intent. A correction-only candidate is exempt; a
  candidate built before the gate is judged at publication with no
  overrides. The `release_state` fixture's partitioned candidate is refused
  against its baseline (the absent dancer the key check closes) and its tests
  approve the rows.
- **WP10, the narrow fence and knobs**
  ([state: publication fences](../reference/state.md#publication-fences)). A
  partitioned candidate is admitted as `publication_narrow`, fencing only
  what its check reads, pinned by a test that runs the check under an
  authorizer. Acquire, plan and derive tick through it; maintain and release
  skip. `release_lag_intervals` (2) holds derive to parse and link units
  while release is behind. A streamed build took no heavy lock (restored by
  the review until measured, `streamed_lock`).
- **WP11, checks** ([operations: verify sample](../reference/operations.md#verify-sample)).
  The weekly verify sample, `swingset partitions --withheld`, and
  `[release-refused]`, `[partitions-withheld]`, `[read-set-miss]`,
  `[artifact-orphans]` and `[release-lag]`.
- **WP12, the watchdog mirror.** `project/year` is a stage clocked like any
  other; a link waits while its event, resolution or a year is queued.
- **WP13.** Records, reference docs and this status. Core: 1,664 passed in
  344 s. Full: 3,189 passed in 978 s. Ruff, mypy and the scope guard: clean. Project and link
  scopes are 175 files (168 at phase 2), build 186 (174).

Found, not fixed:

- A link's resolution state reads its own previous default id, so a forced
  re-run after a revocation differs (`NondeterministicOutputError`).
  Pre-existing; scheduling never re-runs a current link.
- The event-publication report on a partitioned baseline reads closure
  fields (`schedule/event_publication.py`) and reports
  `publication_receipt_or_files_unverified`. Phase 5's dependency report
  should cover it.
- The recorder misses a body read whose SQL text derivation code prepared
  first in the same recording (SQLite's statement cache).
- 1,132 event partitions are withheld as `source_support_unavailable`:
  legacy rows the baseline did not publish (R3). Verified on the drained
  clone: the phase 2 closure candidate on the same state also has 1,801
  events, and production's candidates since 2026-09-24 had 1,796 and 1,797,
  against the baseline's 2,105. So the gate refuses the first partitioned
  candidate with `decrease:events 2105->1801` (R8) until an approval row or
  restored support; the owner decides which.
- Maintain appends `[read-set-miss]` to `ALERTS.log` best effort; the
  watchdog unit runs as root and may own the file (**unverified** on the
  worker), so the watchdog sends its own line.

Rehearsal (WP14), 2026-09-29 and 30, on APFS clones of the 09-29 copy with
no network ([receipt](../../journal/evidence/runtime/architecture-phase3-rehearsal-2026-09/receipt.json)).
Clone A accepted the phase 2 tree, drained every project and link unit (4.1
hours; links re-minted 2,934 generations, 666 MB) and built a closure
candidate, the parity reference. Clone B is A plus this tree. The copy
carried production's cap pause, resumed on A. The Mac interpreter re-queued
every parse (I9): 36,264 parse units stay pending, so drains run project
and link units only, with inventory pacing off, and R6, R7 and R10 run only
their own parse with acquisition backpressure off. Timings are lower bounds
for the worker.

| Check                 | Expected                                                                                                                                                          | Result                                                                                                                                                                                                                                                            |
| --------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| R0, migration 35      | Seconds; 0 `foreign_key_check` violations; growth under 1 MB; retired rows gone; phase 2 code refuses schema 35                                                   | Met except time: 144 s on a cold clone (a re-open at 35 takes 0 s); `foreign_key_check` 133 s, 0 violations; +70 kB; no retired rows were left on the drained copy; refused                                                                                       |
| R1, accept            | Only project, link and build scopes and `version/repository` change; 0 parses; hints for every project and link unit                                              | Met: 13 code scopes, `version/repository` and this change's `config/sources.toml` and `release_decreases.csv`, in 10 s; 0 parses; 35,842 hints plus 437 new-kind units                                                                                            |
| R2, switch drain      | 21 years, 2 mappings, about 765 resolutions mint; no other generation except real changes (listed); about 35,800 equivalences; growth under 250 MB; parity with A | Met except growth: 21 years, 3 mappings, 413 resolutions (one per mapped event; the 352 `unknown` events are calendar rows); 0 other generations, 35,842 equivalences; 2.5 h; parity 0 differences in 7 tables; 384 MB; 2,921 links ready in 11 s, 13 never (F27) |
| R3, partitioned build | Cold under 10 min, `peak_rss` under 2 GB, manifest under 5 MB; a second attempt writes the same manifest digest and rebuilds 0 partitions                         | Time and manifest met, memory not: 463 s, manifest 283 kB, 3,012 partitions (1,132 withheld, 0 multi-event), 69 MB of artifacts; second attempt same digest, 0 rebuilt, 32 s; peak 6.75 GB (F26)                                                                  |
| R4, parity            | B's partitioned candidate equals A's closure candidate except documented differences; `whole` equals streamed on B                                                | Met: fact rows equal (1,801 events each); differences listed below; `whole` equals streamed except per-build columns                                                                                                                                              |
| R5, corrupt event     | Only `event/E` withheld, with its reason; every other partition reused; the closure path fails the whole release (contrast)                                       | Met: only `event/2019-06-neverlandswing` withheld (`invalid_placements`); the closure path builds a release with 0 events, and does so without the corrupt event too (F30)                                                                                        |
| R6, one fetched sheet | 1 parse, then only E's source event and event, `year/<Y>` and `link/event/E`; under 60 s of derive time; the next build rebuilds `event/E` and `year/Y`           | Met: exactly those five units, 8.9 s of unit time (event and link new, source event and year cut off); the next build rebuilt only `event/E`, since the year's rows did not change                                                                                |
| R7, one dancer change | The dancer, the inventory (cuts off, 0 mapping or event generations) and only the links that read it (tens); a name change re-queues only the probed events       | Not met (F28): 0 other generations, but every mapping, resolution, event, source event and year re-ran as an equivalence, and 77 links (40 read the dancer); a name change's dancer run queued 33 links (14 readers), then the same cascade                       |
| R8, gate              | B's candidate passes against the zero-link baseline; one with an event's links removed is refused with its reason; an exact override row passes it                | Partly: refused with `decrease:events 2105->1801`, which the phase 2 candidate shares; an exact row passes it. Confirmed links are 0 on both sides, so one event's rows were removed instead: refused with 7 reasons, passed with exact rows                      |
| R9, narrow fence      | Derive and acquire progress under a narrow publication; a fenced write fails `publication_fenced`                                                                 | Met: derive completed 338 parses, plan and acquire ticked (16 requests); `meta` and `release_manifests` writes failed, `loop_state` passed                                                                                                                        |
| R10, release-lag knob | Release three intervals behind: derive leases no project unit and keeps the guard with its expiry                                                                 | Met: the parse ran, its dancer unit stayed pending, guard `loop:derive release_lag` with a 1 h expiry                                                                                                                                                             |
| R11, verify sample    | 1% of links and every year: 0 mismatches; a seeded mismatch is reported                                                                                           | Clean part met: 30 links and 21 years, 0 mismatches, 84 s. The seeded miss is reported only as a failure, not as `read_set_miss` (F29)                                                                                                                            |
| R12, suites           | Core, full, ruff, mypy and the scope guard: zero failures                                                                                                         | Met: core 1,664 (370 s), full 3,189 (1,014 s), ruff, mypy, scope guard 0                                                                                                                                                                                          |

Differences in R4, B's partitioned candidate against A's closure candidate
(`compare_candidates`): `scope_status` is `accepted` in B and
`legacy_unassessed` in A on the rows of 4 events (events 4, contests 379,
rounds 559, entries 7,796, judges 223, placements 2,636; which label is
right was **unverified** at the time; the review settled it: `accepted`, see
review fixes below); 52 `unmatched` identity links whose entry prints
a WSDC id the registry lacks are closed by the key check (WP8's rule); 4,775
snapshots no row names are left out, and the acquisition counts of 295
coverage rows follow them; per-build columns (`changed_at` in the
changelog, `evidence_cutoff`, `last_changed_at`, `usable_verified_at`).

Rehearsal findings (F26 to F29 blocked the deploy; each is fixed under
review fixes below):

- **F26, build memory.** The partitioned build peaks at 6.75 GB, not under
  2 GB: `build/partitioned.py` `source_support` parses the `recipe_json` of
  every accepted source generation the sidecars name (294,806, about 3.85
  KB each), 3.9 GB of the peak. Event coverage reads only
  `recipe.context.source`. A harness that kept only that field peaked at
  1.93 GB and built the same candidate. _Fixed: support streams with the
  source only; a partitioned build keeps the heavy lock until measured._
- **F27, links that read an absent dancer.** A read key
  `project/dancer/<id>` for a WSDC id with no dancer scope (a printed id the
  registry lacks) becomes a prerequisite that is never current, so the link
  is never ready again once hinted. 13 links on the copy; 3 stayed pending
  after R7. Prerequisites should skip ids without a scope; the pointer hint
  still re-queues the link if the dancer appears. _Fixed as proposed._
- **F28, the inventory still cascades.** An inventory re-run with unchanged
  output upserts its rows (10,071 events and 11,382 series rows) and moves
  the `canonical` and `dancers` revisions, so its cutoff counts as a replay
  (F2) and bumps the inventory's replay epoch; resolutions bump theirs too.
  A dancer change then re-runs 3 mappings, 413 resolutions, about 3,300
  events, 800 source events and 21 years as equivalences, and 32 to 77
  links: about an hour of Mac CPU and 170 to 190 MB of growth per inventory
  run. Units should skip unchanged rows, or the replay rule should ask
  whether values changed, not whether rows were written. _Cause found and
  fixed:_ the inventory wrote `events.sources` and `history_source` as
  `["a", "b"]`, every other writer as `["a","b"]`. On a clone of the diag
  copy one inventory run rewrote 227 rows (222 `sources`, 227
  `history_source`, text only) and a second run none; a resolution or
  calendar that owns such a row rewrote it back, and each rewrite moved the
  `canonical` revision. The inventory now writes the compact form and
  compares these lists by value, so a row that differs only in spacing is
  neither rewritten nor restamped with its parser version. (A first fix that
  only wrote the compact form rewrote 1,557 rows once on the re-rehearsal
  clone and restamped 98 newsletter events, which then lost source support;
  see the re-rehearsal.) An unchanged re-run of every project kind is tested
  to move no epoch. The rest of the 170 to 190 MB was the inventory's and
  the years' content groups, which stored every registry placement row
  (93 MB for the inventory); they now store a row count and digest.
- **F29, the verify sample misses its own signal.** Re-running a link whose
  read set missed an input raises `NondeterministicOutputError`;
  `verify_unit` counts it as `failed`, so no `read_set_miss`, no
  `ALERTS.log` line and no `[read-set-miss]`. It should count as a
  mismatch. _Fixed as proposed._
- **F30, the closure fallback is empty after the switch.** On B the closure
  path built a release with 0 events (dancers and registry placements only),
  with or without the corrupt event. The gate refuses it, but `path =
"closure"` is no longer a rollback; `whole` is.
- **Growth.** The switch drain grew the file 384 MB, over the 250 MB
  estimate: dependency sets 157 MB (the links' new read-set groups),
  `derivation_reads` with its index 80 MB, row refs 53 MB, equivalences 23
  MB. The phase 2 drain on A grew 666 MB.
- **The events decrease.** The gate refuses the first partitioned candidate
  with `decrease:events 2105->1801`, as it would the phase 2 candidate. The
  owner either approves the row or restores support for the 304 events.
- **Tick overhead.** R6's tick took 115 s for 8.9 s of unit work with the
  36,264 re-queued parses in its selection. Production has no such backlog;
  step 4 measures it.

Review fixes (2026-09-30), each with a test that fails without it:

- **Consistent partitions (blocker).** Stage 1 chose partitions by current
  pointers with no check that they agree, so a link that had not re-run
  over a re-minted event published its old identity rows on the renamed
  entries, and a map move built mid-drain could publish one listing's
  results under both the placeholder and the catalog event. An event
  partition is now built only when its event, resolution and link are
  current; otherwise it keeps its last artifact (`kept_not_current`), or is
  withheld (`units_not_current`). A kept artifact whose support was revoked
  since is withheld. A listing two partitions claim stays with the current
  one, else the live map's event (`listing_claimed_elsewhere`). The content
  digest names each link's recorded event generation.
- **Support per observation.** A pair was `accepted` when any accepted
  generation existed for it. It now needs each observation its rows came
  from held, with scope, kind, seq, payload and extract version, by an
  accepted generation, as `closure_support` did. R4's four events
  (`accepted` here, `legacy_unassessed` in the closure) are explained: each
  event row cites an older registry lookup of a re-fetched dancer (dancer
  11162's 09-11 snapshot for `2014-03-west-in-lyon`), whose own accepted
  generations hold the identical observation; the closure credited only the
  generation the unit had selected, the 09-27 snapshot's.
- **F26, build memory.** Support items stream with only
  `recipe.context.source`; the pair lookup reads the recipe's snapshot and
  parser in SQL. An extended test builds stage 1, the support and the
  assembly over 40,000 accepted 4 kB recipes: 0.69 GB on the Mac, 1.41 GB
  with the old loader (bound 0.9 GB).
- **The heavy lock (blocker).** A partitioned build holds it again while
  `[release] streamed_lock` is true (the default), so derive (5G cap, the
  inventory about 4 GB) never runs beside a build that peaked at 6.7 GB.
  It comes off by config after the worker measures the build under 2 GB
  beside derive.
- **F27.** A link's prerequisites keep only the dancers of its read set that
  have a unit (a scope, a queued unit or an observation); the dancer's first
  pointer queues the link.
- **Readiness no longer transitive** ([D-0226](../../journal/decisions/0226-a-failing-or-paced-prerequisite-holds-no-dependent.md)).
  A link waits for no year. A prerequisite that is quarantined, waits for
  input, backs off, or that the pacing gate holds, is settled at its
  pointer, so a dancer change no longer holds every year and link for the
  inventory's 12 hours, and one failing dancer or event freezes nothing.
  The derive tick names them (`settled_prerequisites`); the watchdog mirror
  drops the paced wait of the kinds after the inventory and the link's years.
- **F29 and the verify sample.** A re-run that raises
  `NondeterministicOutputError` is a mismatch (`nondeterministic_output`),
  logged and alerted. Only a complete sample (every kind at its target)
  makes the next one wait a week; the summary has `peak_rss_bytes` and
  `longest_unit_seconds`. The watchdog adds `[verify-failures]`,
  `[verify-overdue]` and `[read-set-widened]` (any `undeclared_reads`,
  `read_set_widened` or `pool_probe_widened` in a day); a widened table's
  digest is computed once per read snapshot.
- **Link load in one snapshot.** The evidence and the read values observed
  at load come from one read transaction; a write between them was
  certified as what the link read.
- **Containment.** Any exception in one partition's stage 1 withholds it
  (`partition_error`); a partition that fails a builder check after
  assembly (identity policy, suppression) is withheld (`assembly_check:<check>`)
  on both paths, its events' associations detached and joins to its
  dancers closed.
- **I6.** The collector removes unretained release manifests and their rows
  and killed builds' `partitions-*`, `closure-*` and `assembly-*` scratch a
  day after; `[artifact-orphans]` counts manifests over a week old. A build
  that finds an artifact collected mid-build runs stage 1 again at once.
- **The gate.** A malformed `release_decreases.csv` no longer stops every
  loop at capture: the build is refused with `overrides_invalid:<line>:
<error>`. An approval row is a floor, so the first approval does not chase
  a count that moves a day later
  ([D-0225](../../journal/decisions/0225-publish-only-strictly-better-releases.md)).
- **Second review (2026-10-02, code and tests only).** A listing claim's
  loser is stored under a digest no reuse matches, so it is contested again
  each build and built once the other claim goes. A generation whose rows
  selection cannot read (`events_of` raising) withholds only its partition
  (`partition_error`). Stage 1 reuses or keeps an artifact only when every
  file matches its sidecar's sha256, a write replaces a damaged copy, and
  `prepare` re-checks after stage 1, so one damaged file costs one rebuild.
  A kept partition's row records `kept_not_current_since`;
  `[partitions-stale]` names those over 48 hours. Assembly withholdings are
  named in the release record (`partitions.assembly_withheld`) and counted
  by `[partitions-withheld]`; `[artifact-collector-blind]` reports a
  collector that could read no retained manifest. A link's load reads each
  subject's previous default and the dancers its decisions name in its own
  snapshot (`dancer_read_after_load` counts a gap). `swingset migrate`
  migrates by hand under the hold. Tests cover a support change alone, the
  streamed path's post-assembly check without suppressions, the gate's
  linked counts, a short verify sample, the derive summary's
  `settled_prerequisites` and a year's revision bump; the fast regression
  tests moved to core. The tree ships `inventory_interval_seconds = 43200`
  and no `history_interval_seconds`.
- **Smaller.** Dancer batches follow the WSDC id the rows carry; migration
  35 deletes the retired units' building rows; the release-lag knob
  engages only when release's ticks were skipped `heavy_busy`, and reports
  other causes; tests for the resolution's proposals group, a year's
  cross-year events, the gate measures, a completed build as release
  progress, a deleted artifact and the source-generation cutoff, and a
  crash at the mapping and resolution checkpoints.

Re-rehearsal (2026-09-30 to 10-02, on the Mac, no network;
[receipt](../../journal/evidence/runtime/architecture-phase3-rerehearsal-2026-09-30/receipt.json)).
Clone A2 is the diag copy with the phase 2 tree, drained (4.0 hours, 666
MB) and built on the closure path (623 s, 7.02 GB), the parity reference.
B2 is A2 plus the fixed tree before its last two fixes (F28's value compare
and the digest groups); C is B2 plus the final tree. The harness limits of
the first rehearsal hold (re-queued parses, inventory pacing off).

| Check                      | Result                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| -------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| R0, migration 35           | 171 s to open and migrate; `foreign_key_check` 183 s, 0 violations; +70 kB; phase 2 code refuses schema 35; a re-open takes 0 s                                                                                                                                                                                                                                                                                                                                                                                       |
| R1, accept                 | 11 s; only project, link and build scopes, `version/repository` and the two changed inputs; 0 parses (the scope guard); hints for every project and link unit                                                                                                                                                                                                                                                                                                                                                         |
| R2, drain and readiness    | 2.8 h; generations only for the inventory 1, mappings 3, resolutions 413, years 21; 37,401 equivalences; 391 MB (dependency sets 160, `derivation_reads` 79, row refs 54). Readiness 2,934 of 2,934 links in 13 s (F27 fixed). Parity with A2: equal except the interim F28 fix's rows (below)                                                                                                                                                                                                                        |
| R3, partitioned build      | Not reliably met: B2 peaked 1.73 GB (was 6.75, F26 fixed) in 611 s cold (stage 1 238, assembly 369); C's three full rebuilds peaked 2.19 to 2.39 GB in 435 to 463 s (below). Manifest 279 kB; 3,012 partitions, 1,782 built, 1,230 withheld (`source_support_unavailable`); 73 MB of artifacts. Second attempt: same digest, 0 rebuilt, 33 to 71 s                                                                                                                                                                    |
| R4, whole path and parity  | `whole` peaks 3.44 GB in 453 s, inside release's 7G with margin, and equals the partitioned candidate except per-build columns. Against A2's closure candidate: R4's four `scope_status` events (explained above), 52 identity links, 4,777 unnamed snapshots, and the interim fix's 98 events                                                                                                                                                                                                                        |
| R6b, settled prerequisites | A dancer change with the inventory paced and one event quarantined: `year/2026` and the sheet's link stay ready; the sheet re-derives its two source events, event, year and link in 8.8 s; neither held unit runs; the tick names both (`settled_prerequisites`)                                                                                                                                                                                                                                                     |
| R7, one dancer change      | F28 fixed: the dancer's one generation, then equivalences only: the inventory, 13 years and the 79 links that read it; no mapping, resolution, event or source event ran; 192 s of unit time; the next build rebuilt only `dancers/20`. A name change queued 33 links (14 readers, 19 by the probe), no cascade                                                                                                                                                                                                       |
| Growth per inventory run   | B2 (before the digest groups): 175 MB for a dancer change, 93 MB of it the inventory's content group. C: 14.5 and 12.1 MB for two dancer changes (dependency sets 14.3 and 11.9 MB); each ran the dancer, then the inventory, 13 years and 79 links as equivalences in 132 to 143 s of unit time, with no replay epoch moved                                                                                                                                                                                          |
| R8b, confirmed links       | A baseline with 500 confirmed links against B2's candidate (0): refused `decrease:confirmed_links 500->0`; an approval row at the candidate's value passes; one above it is refused                                                                                                                                                                                                                                                                                                                                   |
| R11, verify sample         | 30 links and 21 years, 0 mismatches, complete, 129 s; peaks 180 MB (links) and 317 MB (years), longest unit 9.7 s. A seeded read-set miss is a mismatch, alerted as `[read-set-miss]` (F29 fixed)                                                                                                                                                                                                                                                                                                                     |
| R12, build beside derive   | `streamed_lock = false`: a partitioned build (1.75 GB, 456 s, 16 rebuilt) inside a derive that ran the dancer change, the inventory, 13 years and 79 links (4.23 GB process peak). The sum of peaks, 5.98 GB, is under release's 7G plus derive's 5G, not under one cap: the lock stays until step 7                                                                                                                                                                                                                  |
| Suites                     | Core, full (3,246 passed), ruff, mypy: zero failures. B2 ends with `quick_check` ok, 0 running attempts, 0 building rows                                                                                                                                                                                                                                                                                                                                                                                              |
| C, final tree              | The accept re-ran every project and link unit (code-only). The drain (two parts, 5.1 h; the Mac slept in the first) wrote 1 generation (a dancer) and equivalences only; its first inventory moved the inventory and resolution epochs once (below). Readiness 2,934 of 2,934 links in 12 s. A cold build in 445 s, a second with the same digest and 0 rebuilt in 33 s; the candidate equals B2's last except the changed dancer's rows and per-build columns. `quick_check` ok, 0 running attempts, 0 building rows |

C's drain ran the inventory once under the final tree. It rewrote 7 events of
the rolling calendar window that a resolution also writes, which moved the
inventory's replay epoch and then the resolutions', so 3,170 events, 706
source events and 457 links re-ran as equivalences in that drain. The next
two inventory runs (the growth rows) wrote nothing and moved no epoch. The
switch re-runs every unit anyway, so a one-time move costs nothing extra
there; the post-check (step 6) confirms no epoch moves after the drain.

Build memory on C, by phase (process high-water, `rmem.py`): stage 1 to 0.49
GB, the assembly's pass to 1.62 GB (fact counts, coverage), the write to
2.07 GB (the changelog merge). A DuckDB limit of 512 MB instead of 1 GB left
the peak at 2.19 GB, so the growth is Arrow and Python memory, not DuckDB's
pool. B2's 1.73 GB was the low draw. The 2 GB target is therefore not met on
the Mac; the heavy lock stays (`streamed_lock = true`) and release keeps its
7G cap until step 5 measures `peak_rss_bytes` on the worker. Bringing the
assembly under 2 GB is open work, not a deploy precondition.

The interim F28 fix (compact writes only) rewrote 1,557 event rows once
(text only) and restamped 98 newsletter events with the inventory's parser
version; their support then lapsed, so B2's candidates withhold them
(events 1,703, not 1,801) and change the `event_id` of the 7,212 registry
placements that name them. The final fix compares by value and rewrites none; production
never ran the interim fix, so its first candidate should match the first
rehearsal's 1,801 events.

Closing rehearsal (2026-10-02, on the Mac, no network;
[receipt](../../journal/evidence/runtime/architecture-phase3-closing-rehearsal-2026-10-02/receipt.json)).
The final tree, after the second review, from a fresh clone. A3 is the diag
copy with the phase 2 tree: migrated 32 to 34 (339 s), drained in 4.0 hours
(666 MB) and built on the closure path (580 s, 7.66 GB), the parity
reference. B3 is A3 plus the final tree. The harness limits of the first
rehearsal hold (re-queued parses, inventory pacing off). The copy's 2,167
snapshots since 09-25 whose bodies it lacks withhold nothing: no build step
reads a body, and both trees publish the same 1,801 events.

| Check                 | Result                                                                                                                                                                                                                                                                                                                                                                                                    |
| --------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| R0, migration 35      | 152 s to open and migrate, `foreign_key_check` 159 s with 0 violations, +70 kB; phase 2 code refuses schema 35; a re-open takes 0 s                                                                                                                                                                                                                                                                       |
| R1, accept            | 9 s; only the 13 project, link and build code scopes, `version/repository` and the two changed inputs; 0 parses (scope guard 0); 36,279 hints: every project and link unit, the 437 new-kind units included                                                                                                                                                                                               |
| R2, switch drain      | 2.3 h; generations only for the inventory 1, mappings 3, resolutions 413, years 21; 35,846 equivalences; 0 unit failures, 0 `undeclared_reads`; 309 MB (dependency sets 81, `derivation_reads` 79, row refs 54); 2.47 GB peak. Readiness 2,934 of 2,934 links in 10 s. Parity with A3 in 7 tables: equal except one event's `sources` and `history_source` spacing (F28's compact form)                   |
| R3, partitioned build | Cold 474 s (stage 1 197, assembly 273), peak 2.43 GB (over 2 GB); manifest 283 kB; 3,012 partitions, 1,880 built, 1,132 withheld (`source_support_unavailable`), 0 multi-event; 73.5 MB of artifacts; gate refuses `decrease:events 2105->1801`. Second attempt: same digest, 0 rebuilt, 35 s, 0.32 GB                                                                                                    |
| R3d, damaged artifact | One byte flipped in `event/2011-05-swingdiego`'s Parquet file: the next build rebuilt that partition only (3,011 reused) under the same artifact and manifest digests, 47 s                                                                                                                                                                                                                               |
| R4, whole and parity  | `whole` 279 s, peak 4.53 GB (3.44 at the re-rehearsal), inside release's 7G; equal to streamed except per-build columns. Against A3's closure candidate: the four `scope_status` events, 52 identity links, 4,775 unnamed snapshots and per-build columns, as before                                                                                                                                      |
| R5, corrupt event     | A duplicate first place in `2019-06-neverlandswing`. While its units were not current the build kept the last artifact and stamped `kept_not_current_since`; once the event was current and its link re-ran over it, the next build withheld only `event/E` (`invalid_placements`; 1 rebuilt, 3,011 reused) and cleared the stamp. The closure path builds no event at all (F30), so it gives no contrast |
| R6, one fetched sheet | Exactly five units: the parse, `scoringdance:418`, the event, `year/2026` and the link; 8.1 s of unit time (event and link new, source event and year cut off); the next build rebuilt only `event/E` (withheld for source support, as on A3). Tick wall time is the parse backlog's overhead, as in the first rehearsal                                                                                  |
| R7, one dancer change | Dancer 10258 (40 readers): its one generation, then equivalences only for the inventory, 13 years and 79 links; no mapping, resolution, event or source event ran; 121 s of unit time, 14.6 MB; the next build rebuilt only `dancers/20`. A name change queued 33 links (14 readers, 19 by the probe) and no cascade; their drain wrote 14 link generations and 30.7 MB                                   |
| R8, gate              | Refused `decrease:events 2105->1801`; an exact approval passes. One event's rows removed: refused with 7 reasons, passed with exact rows (an approval is a floor, so a lower one passes too). 500 confirmed links against 0: refused; a floor at the candidate passes, one above it does not                                                                                                              |
| R9, narrow fence      | Under a narrow publication: derive completed 286 parses in 124 s, plan 6,426, acquire sent 39 requests; `meta` and `release_manifests` writes refused (`publication fences semantic writes`), `loop_state` passed                                                                                                                                                                                         |
| R10, release-lag knob | Release three intervals behind with no capacity skip: not engaged (`release_not_ticking`), derive runs as usual. With a `heavy_busy` skip: engaged; the parse ran, its dancer unit stayed pending; guard `loop:derive release_lag` with a one-hour expiry                                                                                                                                                 |
| R11, verify sample    | 30 links and 20 of 21 years matched, 0 mismatches (one year not current behind R10's held dancer), 85 s; peak 0.28 GB; longest unit 3.3 s. A seeded read-set miss is a mismatch, alerted as `[read-set-miss]`                                                                                                                                                                                             |
| R12, suites           | Full 3,260 passed (1,123 s); core 1,736 passed (340 s); ruff, mypy and the scope guard clean. B3 ends with `quick_check` ok, 0 running attempts, 0 building rows, 0 active admissions; `diag` unchanged                                                                                                                                                                                                   |

`compare_candidates` needs DuckDB at 4 GB for these candidates: at its 1 GB
default it runs out of memory on the 22 million-row changelog.

Carried forward to the phase lead (review findings, none a deploy blocker):

- **`migrate` on a wrong path.** `swingset migrate --state <typo>` creates a
  fresh database at schema 35 and prints `{"schema_before": null,
"schema_after": 35}`, which looks like a valid preflight receipt. Step 3
  checks that `schema_before` is 34. The command should refuse a missing
  `state.sqlite`, and refuse to run as root.
- **Damage that persists.** If an artifact is still damaged after the one
  inline rebuild (`again = damaged()`), a `BuildError` fails the release,
  against I1. It needs damage that survives a rewrite, such as a disk read
  error. The re-check after stage 1 is also untested: replacing it with
  `broken = []` passes every assembly, partition and release-path test.
- **Environmental errors in stage 1.** Stage 1 catches every exception per
  partition, so an `ENOSPC` or `MemoryError` while one artifact is rewritten
  replaces a good prior artifact with `partition_error`, and the release goes
  ahead without that event. The gate refuses the events decrease unless an
  approval floor covers it, the disk guard runs first, and the next build
  heals it.
- **Reads outside containment.** `_settle_claims` -> `_stale_units` ->
  `derivations.current` for reused claimants (only `RecipeScopeMissing` is
  caught), and `select()`'s JSON reads of one link's dependency manifest or
  one `record_key`, fail the whole release on one malformed system-written
  value.
- **Reporting.** The whole path's `assembly_withheld` keys use `event/` +
  the unit id, so a multi-event partition `event/A+B` is named wrongly and
  counted once per event. `kept_not_current_since` is stamped only when
  content changed: an artifact reused while its units are not current is
  never stamped, so `[partitions-stale]` misses it; and the stamp survives
  units that come back current at the same content digest (a false
  positive). Data is unaffected in both.
- **Writer lock in the verify sample.** `verify_unit` runs a year as one
  write transaction: up to about 10 s on the Mac (longer on the worker)
  other loops back off as `write_lock_timeout`, retried and uncounted. Step
  6 records the sample's `longest_unit_seconds` on the worker.
- **`release_lag()` without an index.** When a build is due it scans
  `execution_admissions` (945,968 rows on the diag copy) on every derive
  tick: 4.8 s cold, 0.4 s warm. An index on `(action_kind, outcome)` fixes
  it.
- **Revocations rebuild everything.** `common["revocations"]` is a global
  digest that the per-partition support inputs already cover: any revocation
  rebuilds about 3,100 partitions. Not wrong, only costly. Phase 3b.
- **Test gaps.** Nothing covers a damaged artifact whose partition is not
  current (withheld `units_not_current`, published again after derive
  catches up; a reviewer probe confirms it). The `_loaded_reads` pool of
  dancers named by identity decisions is unpinned (the
  `dancer_read_after_load` path covers a miss).
- **F28's residue.** On C, 7 rolling-window events written by both the
  inventory and a resolution moved both replay epochs once and cascaded
  3,170 events, 706 source events and 457 links as equivalences. It did not
  recur, but the window rolls. Step 6 detects it; the fix is one owner per
  row.
- **Alert noise.** `[partitions-withheld]` (about 1,230 partitions
  withheld as `source_support_unavailable`) and `[release-refused]`
  (`decrease:events 2105->1801` until the owner decides) fire daily from the
  first build and can hide a real rise.
- **Worker unknowns.** Phase 3 is the first production code that imports
  `duckdb` (step 3 checks it). The worker may miss the 10-minute cold build
  (Mac 435 to 611 s; the worker has less page cache) and will likely miss 2
  GB (Mac 2.2 to 2.5 GB); step 6 says what follows.
- **Evidence and core time.** `mise run evidence-size` fails over the range
  since `trunk()` (about 400 KiB against 200) because phases 0 to 2 are
  unpushed in the same range; phase 3's own three receipts are about 90 KB. Core
  takes over twice the three-minute bar (maintenance item 9).
- **The live overrides.** The new header-only
  `overrides/release_decreases.csv` sits in the Mac checkout, which
  production reads live; phase 1's code ignores it (no re-queue), and
  data-corrections.md warns about such edits.

Deploy steps (operator; one switch):

Preconditions: phase 2 deployed (2a and 2b) and 2b's 24-hour watch passed,
with its stop-marker item settled; the review fixes rehearsed on the final
tree (met 2026-10-02, the closing rehearsal); the owner's decision on
`decrease:events 2105->1801` recorded (an approval row in
`overrides/release_decreases.csv` or restored support); the publish tier's
independent check of the diff and the rehearsal, with a written verdict
(release and publication change); a Mac byte copy at schema 34 verified by
sha256 and `quick_check`; free quota for the drain (about 0.3 to 0.4 GB of
rows) and the artifacts (about 0.1 GB) plus `release_min_free_bytes`; the
file at least 2 GB under the 72 GB cap (D-0220). Production was 70.99 GB on
2026-10-02 04:25 (D-0228), about 1 GB under the cap, so this fails today.
The owner chose on 2026-10-02 to have phase 4's storage work make the room
("Phase 4 before Phase 3"). Phase 4's code builds on phase 3's (migration 36
follows 35), so this switch waits for phase 4 to close, and phase 4's deploy
steps must free room under the cap before phase 3's drain runs (one tree,
or two switches back to back). A cap raise to about 76 GB (D-0228's
fallback) stays the answer if the file nears the cap first. The owner also
approved `decrease:events 2105->1801` the same day; the row in
`overrides/release_decreases.csv` is the owner's to add. Never switch with less: the drain alone
adds about 0.3 to 0.4 GB.

1. Build the deploy tree; its `src/` and `config/` equal the reviewed
   change, whose `config/sources.toml` sets `inventory_interval_seconds` to
   43,200 and drops `history_interval_seconds` (D-0227 and D-0228 end with
   this switch; history is per year, D-0222). A phase 2 tree or hotfix built
   before this switch must keep D-0227's 172,800 for history and D-0228's
   604,800 for the inventory in its own tree. The deploy tree also keeps
   the offers-cache hotfix (in the working copy, not in `9dacbb6`) and
   ships `[release] streamed_lock = true`. Run the scope guard against it
   with the deployed phase 2 tree as baseline: 0 parse or extract scopes
   changed.
2. Hold (owner, reason, review date 24 h out): `operator-hold`, wait until
   no `swingset-*` unit is active; checkpoint the WAL to `(0, N, N)` with an
   empty WAL; byte-copy `state.sqlite` to the Mac (phase 2 code refuses
   schema 35, so the copy is the only way back). Preflight receipt: sha256
   and `quick_check` of both, schema 34, size, pending by stage, queue by
   state, derivation scopes by kind, the published candidate and baseline
   commit (closure mode), the latest candidate id, `[state-size]`, quota
   free.
3. Still under the hold, `nixos-rebuild switch` (timers skip while the hold
   exists, so no unit starts). Then migrate by hand as the service user:
   `sudo -u swingset swingset migrate --lock-timeout 3600`, which prints
   the schema before (34) and after (35) and the seconds; record them in
   the preflight receipt; a `schema_before` other than 34 means a wrong
   `--state` path (the command then creates an empty database). Every
   migration runs `PRAGMA foreign_key_check` under the writer lock: 144 to
   171 s on a cold Mac clone, longer on the worker. Never run it as root: root-owned `state.sqlite-wal` and `-shm`
   files would stop every loop from opening the database. Check `ls -l
/var/lib/swingset/state.sqlite*` shows `swingset` as owner. Phase 3 is the
   first production code that imports DuckDB: check it loads in the units'
   environment ([deployment](../guides/deployment.md#the-phase-3-switch));
   if it does not, every partitioned build fails, so stay on the hold and
   roll back by the byte copy. Then end the hold. Left to the loops, the first tick of any loop migrates (acquire
   included, with its 1G cap and 10 minute timeout) and the others skip
   that tick. The first derive tick accepts the project, link and build
   scopes: 0 parses; hints for every project and link unit.
4. Watch the drain per `AGENTS.md`: every project and link unit re-runs and
   cuts off; years, mappings and resolutions mint once; the inventory may
   wait up to 12 hours (D-0210). Record per kind: units, generations,
   equivalences, seconds; years' staged A and C seconds; `derivation_reads`
   rows; pool probe bumps; `undeclared_reads` and `read_set_widened` (0
   expected).
5. The build of record is the first partitioned build after the drain
   ends (the release loop at the build interval, holding the heavy lock
   while `streamed_lock` is true); if the interval does not fall soon after
   the drain, start it by hand
   ([a second build by hand](../guides/operation.md#a-second-build-by-hand)).
   A build may also land mid-drain: the baseline is from 09-21, so the
   backlog rule does not defer it. Expect it to withhold or keep every
   event whose units are not yet current (`units_not_current`,
   `kept_not_current`) and to hold the heavy lock about 10 minutes, which
   slows the drain; note it, it is not the build of record. Record the
   release record's `peak_rss_bytes` as the memory of record (with the
   loops on each tick is its own process, so it measures only the build;
   `MemoryPeak` counts page cache from the database file and is
   information only, as in phase 2), seconds, partitions built and withheld
   by reason (`assembly_withheld` included), manifest and artifact bytes,
   the gate verdict against the baseline. Then build a second time by hand
   (same section: under the hold, `sudo -u swingset swingset build
--overrides /Users/skeswa/repos/skeswa/swingset/overrides`, the
   services' `overridesDir`, not the store copy, which may hold other
   inputs): the same manifest digest, 0 partitions rebuilt. Compare with the last
   closure-path candidate (`compare_candidates`, as phase 1 step 6) and
   explain every difference.
6. Post-check receipt: the above; file growth attributed, and growth in MB
   per day over 24 hours after the drain, against the projection under
   Cost; `derivation` counts; replay-epoch moves of the inventory and
   resolutions after the drain ended: `SELECT input_key, version FROM
derivation_input_versions WHERE namespace = 'replayed' AND input_key IN
('["project","inventory"]', '["project","resolution"]')` at the drain's
   end and 24 hours later, expected equal; any move is F28 recurring: find
   which writer owns the rows both write; one verify
   sample run by hand, with its `longest_unit_seconds` (a year holds the
   writer lock that long); watchdog lines seen; R5, R9 and R10 by observation
   (a withheld partition withholds only itself, fetching continues during
   a build, `release_lag` engages only on `heavy_busy`). Acceptance of
   record: a fetched sheet in production re-derives one event, its links
   and its year (from the derive ticks' unit lists), and a partitioned
   build under 2 GB (`peak_rss_bytes`) and 10 minutes on the worker. If
   the worker misses either, record it and keep the heavy lock and the 7G
   cap; the memory item and changelog compaction (known limits) become
   plan items. Neither is a reason to roll back.
7. After the post-check, if the measured `peak_rss_bytes` allows: lower
   release's `MemoryMax` from 7G to 3G in a `nix/`-only switch (no recipe
   change), then set `[release] streamed_lock = false` (a config switch,
   no re-queue) and check the next build's `peak_rss_bytes` beside an
   active derive, the inventory included.
8. Phase 3b, after one partitioned candidate is post-checked and the owner
   agrees: remove `path = "whole"` and `"closure"`, `closure.select`,
   `closure_rows` (after moving what the partition modules import from it),
   the closure support modules once no retained candidate is closure-mode,
   and their tests. A code change with its own small deploy.

Rollback: to phase 2 is step 2's byte copy by
[rolling back a migration](../guides/operation.md#rolling-back-a-migration);
work since the switch is lost and re-fetched. Short of that, `[release]
path = "whole"` for an assembly bug, a `nixos-rebuild switch` that
re-queues nothing, but only when its measured peak fits release's
`MemoryMax` with margin (see the re-rehearsal's R4); otherwise it also needs
the cap raised and derive stopped for that build. The `"closure"` path
built an empty release on the rehearsal clone after the switch (F30), so it
is no fallback for an artifact bug; the byte copy is.

Cost the operator faces: a hold of about an hour (the WAL checkpoint, the
byte copy of the 65 to 72 GB file to the Mac with its sha256 and
`quick_check`, the switch, and `migrate`, 2.5 to 3 minutes on a cold Mac
clone and longer on the worker); one re-run of every project and link unit
(about 35,850: 2.3 to 2.8 hours on the Mac, plus up to 12 hours of
inventory wait), all cut off except the 438 new units' first generations;
growth about 0.3 to 0.4 GB (R2: 309, 384 and 391 MB, mostly dependency sets
and `derivation_reads`); about 0.1 GB of artifacts on the quota (R3: 69 to
74 MB); a first build that holds the heavy lock about 8 to 10 minutes; a
gate refusal (`decrease:events 2105->1801`) on every build until the
owner's decision is in `release_decreases.csv`; daily `[partitions-withheld]`
and `[release-refused]` lines; and the independent check. After it: a
fetched sheet re-derives one event, its links and its year (R6, 8 s of unit
time); a dancer change re-runs the inventory, its years and the tens of
links that read it, all cut off, not today's cascade of 2,933 links and
3,400 project generations (about 0.7 GB each since D-0228's receipt). Growth
per day after the switch, estimated, not measured (step 6 measures it): each
inventory run that carries dancer changes cost 12.1 to 14.6 MB on C and B3,
almost all dependency sets (the inventory's, its 13 years' and its 79 links'
equivalences). The inventory runs at most twice a day at 43,200 s, but a
calendar generation lifts its wait (D-0210), which is why the diag copy
shows 12 to 14 runs a day carrying 80 to 194 dancer generations (2026-09-26
to 28). A run with many changed dancers reaches more links, so the estimate
is 0.15 to 0.4 GB a day (links the larger and less certain part), against
about 2.9 GB a day before D-0227; builds peak 1.7 to 2.5 GB on the Mac (R3,
C, B3), under release's 7G with the heavy lock held; a partitioned
publication no longer pauses fetching (R9).

Known limits after phase 3:

- The inventory is still one paced unit of about 4 GB (F18), against
  derive's 5G cap.
- A journal or identity decision change re-runs every link (an
  over-approximate key).
- The changelog is rewritten whole each build: 22.1 million rows at R3,
  growing about 250,000 a build. A cold build took 435 to 611 s on the Mac
  (assembly 264 to 370 s), so compaction stays a later item.
- A partitioned build holds the heavy lock while `streamed_lock` is true:
  derive waits for it (about 10 minutes a build) until step 7 measures the
  build beside derive on the worker.
- A cold partitioned build peaks 1.7 to 2.5 GB on the Mac, over the 2 GB
  target: the assembly's fact counts and coverage, then the changelog merge,
  hold Arrow and Python memory beside DuckDB's pool. The heavy lock and the
  7G cap stay until that is cut and measured.
- Retention keeps the older generations a kept generation names now that F1
  is gone, until phase 4.
- The three release paths coexist until phase 3b; the closure path
  withholds years that reach superseded generations (F22), every event on
  the rehearsal clone (F30).
- The recorder's blind spots (F25, the statement cache) reach production;
  the weekly 1% sample is the only net.
- Artifacts live in `state/partitions/`, outside `gc` and the blob store,
  until phase 4.

**Phase 4: storage lifecycle and backups (about 2 weeks).** _Trigger:_ start
it, or raise the quota, when the state without `checkpoints/`, `venv/` and
`uv-cache/` passes about 55 GB; one local checkpoint then leaves little room
(D-0203). The watchdog's `[state-size]` uses that measure and fires 5 GB
earlier, at 50 GB; post-check receipts carry the size. Payloads, parse
results and manifests to the blob store with a lifecycle row per class;
streaming mark-sweep across all classes, including partial derivation
generations older than a day that no pointer, equivalence or building row
names (D-0217); segmented incremental backup with
weekly automated restore verification; migration of retained
`derivation_payloads` out of the file, then reclaim. Deletes data, so it takes
the independent check. _Accept when_ the file is under 15 GB, the daily backup
uploads and verifies, the restore drill passes on the worker in scratch, and a
month of receipts shows the blob store within budget.

_Phase 4 status: implemented offline (closed 2026-10-03), not deployed._
Work packages WP1 to WP7 landed 2026-10-02 and 03, were rehearsed on a
real-data clone (WP8), reviewed in three rounds with fixes, and rehearsed
again on the final tree (the closing rehearsal, table below). Offline, the
file-size accept-when is not met: 31.8 GB after reclaim, not under 15 GB
(F39, W1, open decision 4). A local set and its offline drill pass on the
Mac; the upload, the drill on the worker and the month of receipts wait for
the deploy. Records:
[D-0229](../../journal/decisions/0229-derivation-output-and-bodies-live-in-packs.md)
(output and old bodies live in packs; replaces D-0149's gate and the
bounded-state plan's step 4; amends D-0119),
[D-0231](../../journal/decisions/0231-collect-every-unnamed-dependency-set-and-unrooted-object.md)
(the collector; extends D-0200) and
[D-0232](../../journal/decisions/0232-segmented-backups-with-a-weekly-restore-drill.md)
(backup sets and the weekly drill; replace D-0203 at the switch) and
[D-0234](../../journal/decisions/0234-the-backup-archive-collects-unnamed-segments-and-squashes-its-history.md)
(the archive collects unnamed segments and squashes its history). Migration
36 adds the object index, the eviction and pack markers, the storage
authority and the backup tables ([schema history](../reference/schema-history.md)).
Reference: [state: objects and packs](../reference/state.md#objects-and-packs)
and [backup sets](../reference/state.md#backup-sets); procedure:
[operation: packing cold output](../guides/operation.md#packing-cold-output-phase-4).

Eleven findings changed the phase as written, measured by read-only queries
on the 2026-09-29 copy (schema 32, 64.7 GB):

- **F31, payload bytes are not the problem.** `derivation_payloads` is
  1.45 GB. The bytes are row references and their indexes (29.2 GB),
  dependency sets (16.2 GB) and parse JSON (7.1 GB). Moving payloads out
  means moving whole generations' output out: references and payloads
  together.
- **F32, one pack per distinct output, kept forever.** A pack is the label
  stream itself, so its sha256 is the label's `output_digest`. It needs no
  mapping table, verifies against the label, and dedupes about 20× (30,065
  packs for 598,077 evicted labels). Packs replace the
  [bounded-state plan](bounded-state-and-archive.md)'s two remote stores
  (its step 4); the segmented backup is the second copy.
- **F33, resident is not complete.** Four checks called a generation
  partial when it had fewer references than `row_count`; an evicted one has
  none. A `derivation_evicted` marker says so, and every check reads
  "resident or evicted".
- **F34, the partitioned build joins current pointers in SQL.** Pointers
  and building generations stay resident. Every other reader goes through
  one accessor that falls back to the pack, and a pointer flip to an
  evicted generation pages it in first.
- **F35, parse-scope code reads old parse generations** (cutoff, the
  revocation chain, recipes), and those files may not change. Only
  `manifest_json`, `result_json` and `report_json` of unprotected rows are
  packed. The protected set covers what parse code reads, and a stub read in
  parse code fails closed (a test per reader). The review added two rules:
  every `generation_id` anywhere in a retained closure proof, not only in
  `source_support` (a closure-mode baseline's `event_coverage` named 475
  generations, 449 of them otherwise unprotected), and `event`, the
  generations the kept `event_*` subsystem reads (enumerations, their
  parents, their members' claims: 4,560 on the diag copy). Packed, they
  failed `closure.validate` with `event_coverage_support_changed`.
- **F36, reclaim refused at 72 GB.** It asked for twice the file, about
  145 GB. A rewrite needs about twice the bytes in use, and its temporary
  files must not go to the worker's small `/tmp`. It now asks for
  `2.5 × bytes in use + 12 GB` (W3) on the smaller of statfs and the btrfs
  quota (statfs cannot see the quota), and writes under `<state>/tmp`.
- **F37, build recipes.** 24 `build/release` labels carry 1.78 GB of pinned
  closure in `recipe_json`. Those over 1 MB are packed like parse bodies.
- **F38, the 15 to 17 GB projection was wrong.** It counted the large
  dependency sets as freed (F39). The rehearsal measured 31.6 GB.
- **F39, most dependency-set bytes are named.** The 15.8 GB that no
  generation names directly is named through other sets: proofs by build
  labels' `release_closure` members, `input_row` sets by old inventory
  labels. The collector frees 598,088 sets, 0.10 GB. Freeing the rest is
  open decision 4. The collector still runs on schemas 33 to 35 (under
  `operator-hold`), but as a hotfix it would free too little to matter.
- **F40, local blob recovery read only full checkpoints.**
  `SegmentRecovery` reads local set segments, after
  `LocalCheckpointRecovery`, wherever the loops, the cycle and the CLI
  build recovery.
- **F41, restore checks the public hub.** The drill and the rehearsal
  install offline (`restore_set.install`) and never activate.

What each package built:

- **WP1, object store and migration 36.** Content-addressed files under
  `objects/<class>/`, gzip except raw release manifests, written through
  `.writing-*` and fsynced; one `objects` row each and one `blob_classes`
  policy row per class. A storage authority (the D-0136 pattern) gates every
  delete and stub. The scope guard showed 0 parse or extract scopes changed
  after every package.
- **WP2, output packs.** `state/output_packs.py`: `pack`, the accessor
  (`rows`, `row`, `table_rows`; label checked) and `page_in`. A static test
  lists the modules that may name the row tables in SQL.
- **WP3, eviction and reclaim.** `storage plan|apply`: a plan digest,
  packs, verification, then one exclusive move that rebuilds
  `derivation_row_refs` with the kept rows. `storage.evict` is maintain's
  bounded version. Reclaim per F36.
- **WP4, body packs.** Parse bodies and build recipes over 1 MB, the
  protected set, the reader audit and `storage restore-body`. Two rules
  beyond the spec: an admission review's cohort is protected, and the
  7-day rule covers build recipes too.
- **WP5, the collector.** One streaming mark-sweep over the object classes,
  release manifests, partition artifacts, dependency sets and orphans, with
  `gc --collect --plan|--apply`. It found three set roots the spec missed:
  a scope's `materialized_signature` proof, a recipe's continuity set, and
  sets named inside another set. Release manifests are objects now;
  `storage apply` moves phase 3's files. A synthetic 200,000 labels,
  100,000 objects and 300,000 sets peak at 107 MB.
- **WP6, segmented backups.** `backup --segmented`, `restore --backup-set`
  and `SegmentRecovery`. `VACUUM INTO` runs before the control lock, not
  under it, since every database open waits on that lock. 50,000 files and
  a 218 MB database peak at 173 MB.
- **WP7, maintain, watchdog and nix.** Each maintain tick evicts and packs
  a bounded batch; the collector sweeps weekly; the drill installs the
  newest set weekly
  ([maintain storage tasks](../reference/operations.md#maintain-storage-tasks)).
  New lines `[restore-drill-failed]`, `[restore-drill-stale]`,
  `[collector-failed]` and `[objects-size]`; `[backup-stale]` reads sets;
  `[incomplete-generations]` counts partial generations that still hold
  references. Nix: `backupSegmented`, `SQLITE_TMPDIR`, and a 60-minute
  maintain budget with a 90-minute timeout.
- **Review fixes** (2026-10-03): each maintain storage task is isolated (one
  that raises is `{failed, error}`, `[storage-failed]`), a generation or body
  that fails is counted with a guard and the pass goes on, the size cap and
  controls come first, and `backlog` with no progress for three ticks is
  `[storage-stalled]`; `[collector-stale]` past `collect_every_days` plus 2.
  A damaged pack is written again from a resident generation, and a lost
  pack of an evicted generation is rebuilt by a unit that reuses it. The
  drill holds the backup's lock, needs 20 GB of headroom, records its start,
  and streams a closure proof's sha256 instead of decoding it; maintain runs
  at 05:30. A set checks its own room by bytes in use, cleans a killed run's
  scratch, records missing files (`[backup-incomplete]`) instead of failing,
  and the archive collects and squashes (D-0234; `[archive-size]`,
  `[backup-stale]` in upload mode). Moves and deletes run with
  `secure_delete` off (W2), apply checks its room, and the collector moves a
  victim aside inside its transaction so a writer never names a removed
  file.
- **Second review fixes** (2026-10-03), each with a test that fails without
  it: a lost `objects` file comes back from the newest local set that holds
  it, in the backup and in each evict pass (`storage recover-objects` by
  hand); failing that an earlier held segment stands for it, so one lost
  pack no longer leaves every later set unrestorable. A corrupt new file is
  left out under `missing` and the set still written. A generation that
  always fails waits out a retry window (1, 2, 4, 7 days) and its rows
  count against the budget, so it never starves later ones; a full disk
  stops the pass. A refused body pass is the task's `error` with a guard.
  Sets upload to their own repository, `skeswa/swingset-backup-sets`, whose
  collector refuses to squash while it holds anything a set did not write
  (D-0234); the checkpoint archive's history stays. The collector isolates
  each class and leaves manifests and artifacts alone while a set is
  written; verify is isolated and an overdue drill runs before it; a failed
  drill drops its restored database; the partitioned build's selection reads
  one snapshot; a unit that may return to a large evicted generation is
  staged; tests no longer read the worker's quota file. The third round
  re-ran the earlier probes: a shed after a concurrent page-in returns
  without touching the generation, so none is left partial and unmarked.
- **WP8**, the rehearsal below; **WP9**, these docs.

Closing rehearsal, 2026-10-03, of the final tree (after the third review
round) on one APFS clone of the 2026-09-29 copy (schema 32), no network
([receipt](../../journal/evidence/runtime/architecture-phase4-closing-rehearsal-2026-10-03/receipt.json)).
WP8 ran the same steps on the WP7 tree
([receipt](../../journal/evidence/runtime/architecture-phase4-rehearsal-2026-10/receipt.json));
its findings W1 to W6 are below. Timings are on a loaded Mac, lower bounds
for the worker. The copy's 4,228 never-copied files stay out of the backup
closure. The Mac interpreter re-queued every parse, so no drain ran, and
2,934 event partitions stay withheld (`units_not_current`) in both builds;
the gate refuses them (`decrease:events 2105->0`), as in WP8.

| Step                  | Expected                                                                                                                       | Result                                                                                                                                                                                                                                                                                                                                           |
| --------------------- | ------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| R0, migrate           | 32 → 36 with seconds per migration; 0 `foreign_key_check` violations; phase 3 code refuses 36                                  | Met: 960 s in all, 0 violations, +48 MB. On a second clone: 33 to 35 (phase 3 code) 510 s, 36 182 s; each migration's own foreign key check takes about 170 s, so migration 36 itself is about 10 s. Phase 3 code refuses 36                                                                                                                     |
| R1, build before      | A partitioned build; manifest D1                                                                                               | 576 s (partitions 210, assembly 355), 695 MB; 57 partitions built, 2,934 withheld; D1 `800b1a29`                                                                                                                                                                                                                                                 |
| R2, storage plan      | Twice, one digest, under 1.5 GB; about 30,510 packs, 41 M references evicted, 2.8 M kept, about 260,000 parse rows, 24 recipes | Met: 1,280 and 1,142 s, 884 and 1,112 MB, one digest; 30,065 packs, 598,077 generations and 41,255,315 references evicted, 2,796,109 kept; 193,865 parse rows (the `event` rule keeps 4,560) and 11 recipes, since protection keeps more than estimated                                                                                          |
| R3, storage apply     | Seconds per stage and object bytes; resumes after a SIGKILL; a rerun returns the receipt                                       | Met: killed at 3,282 packs, no `.writing-*` left; the resume took 1,508 s (plan 424, pack 202, verify 18, bodies 233, move 512) at 1.13 GB; WAL peak 2.2 GB, room asked 13.7 GB. The rerun took 0.5 s, same receipt. Objects 464 MB: 30,065 output packs 191 MB, 40,973 parse bodies 104 MB, 11 recipes 169 MB                                   |
| R4, collect           | About 920,000 sets, 15.8 GB, under 1.5 GB                                                                                      | Bytes not met (F39): 598,088 sets, 0.10 GB; plan 66 s, apply 221 s, 172 MB; a second plan names nothing                                                                                                                                                                                                                                          |
| R5, reclaim           | File 15 to 17 GB; `integrity_check` and the foreign key check clean                                                            | Size not met: 64.8 to 31.8 GB in 1,036 s, of which 16.1 GB is named dependency sets (W1). It asked for 93.6 GB (`2.5 × 32.6 GB + 12 GB`) and used about 64 GB at peak (WAL 32 GB). `integrity_check` ok in 223 s, 0 violations                                                                                                                   |
| R6, correctness       | D1 again with 0 rebuilt; accessor 100%; bodies byte-exact; a 2,000-unit derive sample with 0 failures, page-ins counted        | Met: D1, 0 of 2,991 rebuilt, 60 s, 236 MB; 1,000 of 1,000 evicted generations equal their labels (60,707 rows); 200 of 200 packed bodies restore byte-exact; one derive tick of 3,070 units, 0 failures, 0 page-ins, 0 pointers to evicted generations, 992 MB; verify 30 of 30 links; doctor (2.23 GB) and `gc --plan --candidates-only` exit 0 |
| R7, backup            | Parts under 4 GiB; a derive tick beside `VACUUM INTO` waits on nothing; a second set holds only new files                      | Met: 241 s (`VACUUM INTO` 147 s), 280 MB, room asked 51.9 GB; one 3.34 GB part; 137,366 files (1.29 GB) in one 1.40 GB segment, 0 missing. The tick opened in 1.5 s and ran 2,880 units, 0 failures, longest write 1.8 s. A second set took 132 s: 1 new file (1,278 bytes) and a new 3.36 GB part (W6); the first set pruned                    |
| R8, restore and drill | Offline install, a manifest hydrated, opens at 36; the drill passes; a corrupt part fails it with the alert                    | Met: install 190 s (`quick_check`), the partitioned manifest hydrates to D1 (57 artifacts), schema 36, 0 violations, 33.2 GB target. The drill passed in 189 s at 204 MB (need 53.2 GB). A maintain tick then took 84 s at 566 MB, nothing failed. With one byte of a part flipped the drill failed in 1.5 s and wrote `[restore-drill-failed]`  |
| R9, memory            | Each step under 1.5 GB                                                                                                         | Met by phase 4's steps (highest: apply 1.13 GB, plan 1.11 GB). Derive ticks peaked 0.89 to 0.99 GB. Doctor (2.23 GB) exceeds it, as before phase 4 (publication report)                                                                                                                                                                          |
| R10, suites           | Core, full, ruff, mypy and the scope guard: zero failures                                                                      | Met: core 1,938 passed in 440 s, full 3,467 passed in 1,217 s, ruff and mypy clean (277 files); 0 parse or extract scopes changed; project and link scopes 175 to 179 files, build 186 to 190                                                                                                                                                    |

The maintain tick after R8 (60-minute budget, a passed set, nothing old and
cold): `evict` 7 s with no work (its lost-object scan of 71,050 rows
included), `pack_bodies` 11 s, the collector 39 s (5,950 sets, 1.0 MB, from
the derive ticks), verify 20 s; the drill was not due. The staging rule for
units over `CHUNK_ROWS` ran in both derive ticks (5,950 units) but no unit
reused an evicted generation, so no page-in measured it.

Rehearsal findings (WP8; W2, W3 and W5 fixed by the review, the closing
rehearsal confirms them):

- **W1, the file stays about 32 GB.** By `dbstat` after reclaim: dependency
  sets 16.06 GB, `source_generations` 3.67, row references 1.71,
  `identity_link_history` 1.68, `execution_dependencies` 1.41, payloads
  1.36, labels 1.33, the rest about 4.6. Without the named sets the file
  would be about 15.8 GB, and phase 5's `execution_*` (1.7 GB) would take it
  under 15 GB. Open decision 4.
- **W2, fixed: the move wrote a WAL as large as what it drops** (29.7 GB in
  WP8). SQLite here, and nixpkgs' on the worker (compile option
  `SECURE_DELETE`, confirmed by the review), zeroes every freed page through
  the log. The move, eviction, body packing and the collector now run with
  `secure_delete` off (`disk.fast_delete`); a test drops a table both ways.
  Apply checks its room first and truncates the WAL after the move. Closing
  rehearsal: WAL peak 2.2 GB, move 512 s against WP8's 926 s.
- **W3, fixed: reclaim asked for less than it used.** WP8 used 76.3 GB for
  32.4 GB in use (2.35 times). It asks for `2.5 × in use + 12 GB` now, on
  the quota as well as statfs. Closing rehearsal: asked 93.6 GB, used about
  64 GB (1.95 times).
- **W5, fixed.** A storage receipt's `started_at` and `finished_at` come
  from the wall clock.
- **W6, a daily set is mostly the database:** a new 3.4 GB part each day
  while new files are kilobytes. Evicted output reaches objects only
  through maintain's eviction.

Review-fix rehearsal, 2026-10-03, on two more clones of the same copy, Mac,
no network ([receipt](../../journal/evidence/runtime/architecture-phase4-review-2026-10-03/receipt.json)):

- Migrate 837 s, 0 foreign key violations. The storage plan took 839 s at
  518 MB: the same 598,076 generations, 41,255,314 references and 30,064
  packs, but 193,865 parse rows, not 198,425: the `event` rule keeps 4,560.
- Apply took 1,418 s (plan 371, pack 233, verify 22, bodies 221, move 481 s,
  half R3's) at 596 MB. With `secure_delete` off the WAL peaked at 2.2 GB,
  not 29.7 GB. Its room check asked for 13.7 GB (1.85 GB of kept references
  twice, plus 10 GB). Bytes in use after the collector: 32.6 GB, so reclaim
  would ask for 93.6 GB.
- The re-review repeated R7 and R8 on its own clone of the fixed tree (now
  deleted). A set: 253 s (`VACUUM INTO` 162 s), 277 MB, room asked 52.7 GB
  for 32.7 GB in use, one 3.43 GB part, one 1.15 GB segment, 137,167 files,
  0 missing. A drill of the closure-mode baseline `cand_80719e178655435d`:
  passed in 189 s at 191 MB (cap 2 GB), a 223 MB proof streamed, need
  53.1 GB. Apply again gave digest `a83d85cd` in 1,459 s, WAL peak 2.23 GB,
  971 MB by `/usr/bin/time` (the plan 903 MB against the receipt's 518 MB;
  both under 1.5 GB). Production's first set and drill, before reclaim,
  have 36 to 40 GB in use, so each needs about 57 to 61 GB free.
- A derive sample (5,690 units, 0 failures, 0 page-ins, no growth: new rows
  reused free pages) and then one maintain tick with a 60-minute budget:
  324 s, 510 MB, nothing failed. Nothing was old and cold, so `evict` found
  no work in 11 s; `pack_bodies` 96 s; the collector's first sweep 185 s
  (603,777 sets, 0.10 GB); verify 30 of 30 links.
- Eviction throughput, on a second clone migrated and not applied (the day
  after a large supersession): one `evict` of the 2,000,000-reference budget
  moved 1,991,822 references of 28,154 generations in 464 s (about 4,300 a
  second) at 83 MB, longest write 1.6 s, WAL under 32 MB; 569,922
  generations were left, so a backlog that size drains in about three weeks
  of daily ticks. `pack_bodies` packed 20,000 parse rows and 11 recipes in
  88 s at 572 MB. The worker is slower (**unverified**).

Review verdict (three rounds, 2026-10-03): no blocker or major open. The
reviewers re-ran their earlier probes against the final tree, and each fixed
finding's probe now fails as intended (copies under the workflow scratch). They
read storage apply's move, eviction, the collector's sweeps, body stubs,
segmented sets, restore and prune and found nothing that loses data, publishes
a wrong dataset, stalls production or blocks recovery. Their conditions for the
deploy:

- The publish tier's independent check reads this diff, the closing rehearsal
  receipt and the review receipt, with a written verdict.
- Deploy feasibility, checked against status (72.66 GB file, 76 GB cap,
  76.9 GB quota free): apply needs about 14 GB; a set needs 56 to 60 GB and a
  drill about 61 GB on the quota before reclaim, about 10 GB of margin;
  reclaim waits for the checkpoint removal (step 6); rollback after step 4 is
  only the byte copy.
- Sets upload only to `skeswa/swingset-backup-sets`, which the owner creates
  private; nothing checks that it is private (D-0234).
- The core suite took 795 to 1,055 s on a loaded Mac, and 440 s at the
  close (load about 6), against `AGENTS.md`'s three minutes; trim it
  (maintenance queue item 9).

Carried forward (minor; none loses data, publishes a wrong dataset, stalls
production or blocks recovery). Maintenance queue items, in order of use:

- Reads. `output_packs.row()` and `table_rows()` check `evicted()` and then
  read in a second statement; a shed committing between them returns `None`
  or a short list. `build/generations.completed()` can then make a publish
  refuse wrongly (fails closed). `_dancer_rows` skips an evicted dancer
  pointer instead of failing closed (only if F34 breaks).
- Page-in. A staged reuse's step B pages in without a `derivation_building`
  row, so eviction may shed or re-evict it before step C; C raises
  `SupersededWorkError` and the run retries. Wasted work, rarely reached.
- Body packing. The retained-candidate protection is read once per pass, so
  a closure-mode candidate written during it is not seen (fails closed at
  closure validation). Only `BodyUnavailable` is caught around the stub, so a
  collector unlink of a reused rowless file aborts the task
  (`[storage-failed]`, rolled back). With 100 or more bodies always failing
  at the head of the order, every pass makes no progress (`[storage-stalled]`
  reports it; no retry window as evict has).
- Retry windows. `_Retries.save` writes `.evict-retry.json.tmp-<pid>` with
  mode `xb`; a leftover from a crash and a reused pid make every save fail
  silently, and the starvation fix stops working.
- Drill timing. The drill runs only with 1,800 s of the 3,600 s budget left,
  after evict, body packing and the collector; a long backlog can skip it as
  `short_budget` until `[restore-drill-stale]` at 8 days.
- Missing files. A set that lists `missing` cannot be installed and has no
  override; an object lost before any set held it makes every later set fail
  its drill daily, and in upload mode three such sets leave no remote set that
  restores. `restore_lost` checks only that a file exists, so a present but
  corrupt pack of an evicted generation is never repaired (readers fail
  closed; a pinned closure-mode baseline's validation can stop a build).
  `_sweep_objects` does not take the backup lock, so an object removed
  between a set's `VACUUM INTO` and its listing goes under `missing`.
- Cost that grows. Each evict pass runs `restore_lost` over every `objects`
  row and rescans partial labels at 0 refs (about 70k stats now); the
  watchdog walks `objects/`, `blobs/`, `extracts/` and `partitions/` twice.
  `_snapshot` runs lost-object recovery under the control lock, so many lost
  objects make every open wait. `SegmentRecovery` builds its lookup database
  under the default `TMPDIR`, which is the small `/tmp` for derive, acquire
  and the cycle on the worker.
- Alerts. `[storage-stalled]` counts retry-window and lost items, so one
  waiting generation fires it daily with a wrong hint; body packing's backlog
  counts protected rows. `_beside_backup` takes `loop-backup.lock`, so a
  backup timer firing during maintain's sweep or drill skips the day's set
  (D-0192).
- Not measured with a page-in: the staging rule for units over
  `CHUNK_ROWS` (about 113 units on diag would stage). The closing
  rehearsal's two derive ticks and builds ran it and the single-snapshot
  `_select` with 0 failures, but no unit reused an evicted generation.
- Test gaps (surviving mutants): P04 (`Store.install`'s digest check), P09
  (refusing a set that lists `missing`), P12 (zero padding of a file that
  shrinks during the tar read), P21 and P26 (`SegmentRecovery._read`'s length
  check, `recover_object`'s index filter), N45 (`tick.stop()` after the cap
  and controls); M37 is equivalent.
- Tooling. Parallel pytest runs share the default basetemp and prune each
  other's temp dirs (false failures in the drill tests). `mise run
evidence-size` fails at 490 KiB against `trunk()` because phases 0 to 4 are
  unpushed in one stack; phase 4's own evidence is within the 200 KiB budget.

Deploy steps (operator; one switch with phase 3):

Preconditions: phase 2's switch 2b (the loops) for step 8 and after:
maintain runs eviction, body packing, the collector and the drill only as a
loop. Steps 3 to 6 are CLI commands and may run under 2a's cycle mode, which
reads output through the same accessor; until 2b nothing evicts the output
that goes cold after the switch. Phase 3 goes in the same switch: phase 4's
tree contains it (migration 36 follows 35), and the owner chose phase 4
before phase 3 on 2026-10-02 so that these steps free room before phase 3's
drain. Phase 3's preconditions and deploy steps apply, except its cap
precondition, which steps 4 to 6 meet. The publish tier's independent check
of the diff, the closing rehearsal's receipt and the review-fix receipt, with a written
verdict (this deletes data and changes backup and restore). A Mac byte copy
at schema 34 verified by sha256 and `quick_check`. Preflight receipt items:

- Quota room, read with `sudo swingset-disk` (statfs cannot see it): the
  move needs twice the kept references' bytes plus 10 GB (about 14 GB on
  diag; apply checks it); reclaim needs `2.5 × bytes in use + 12 GB` after
  collection, about 100 to 112 GB for 36 to 40 GB in use, against 76.9 GB
  free on 2026-10-03 (D-0233). So reclaim waits unless room is made: remove
  old local checkpoints by name first, with an independent check as
  D-0203's switch did. Waiting is safe (step 6).
- Mac room, measured with `df` on the Mac: the byte copy (about 73 GB), the
  move (small with `secure_delete` off), the first set under
  `checkpoints/` (a 3.4 GB part and a 1.2 GB segment) and the drill's
  scratch under `/var/tmp` (the bytes in use, 36 to 40 GB before reclaim),
  and later reclaim's peak. Each may grow the OrbStack disk image on APFS,
  up to its 100 GB, if those blocks were never allocated (**unverified**);
  plus 20 GB. The Mac had 155 GiB free on
  2026-10-03; the 2026-09-18 outage was a full Mac disk. Delete first, by
  name: `~/swingset-offload/state-20260929.sqlite` (61 GiB) once the diag
  copy no longer needs it, and the schema 33 byte copy once the schema 34
  copy is verified.
- Cap runway: the file was 72.66 GB at 2026-10-03 04:24 and grows about
  1.3 GB per daily dry-run build against the 76 GB cap, about 2 to 3 days.
  The no-requeue stopgap is a longer `build_interval_seconds` (config only,
  as D-0220 set it), not another cap raise.

1. Build the deploy tree (phases 3 and 4, `backupSegmented = false`; its
   `max_database_bytes` is production's 76 GB, D-0233). Run the scope guard
   with the deployed phase 2 tree as baseline: 0 parse or extract scopes
   changed.
2. Hold (owner, reason, review date 24 h out): `operator-hold`, wait until
   no `swingset-*` unit is active, checkpoint the WAL, byte-copy
   `state.sqlite` to the Mac (phase 2 code refuses 35 and 36: the copy is
   the way back). Preflight receipt: sha256 and `quick_check` of both,
   schema 34, size, quota free, the published and baseline candidates; the
   `dbstat` top 10 from the Mac copy.
3. Still under the hold: `nixos-rebuild switch`, then
   `sudo -u swingset swingset migrate --lock-timeout 3600` (34 → 36; R0 took
   16 minutes for 32 → 36 on the Mac, almost all of it one foreign key
   check of about 170 s per migration, so 34 → 36 is about 6 minutes). Check that `swingset` owns
   `state.sqlite*`, and phase 3's DuckDB check.
4. `sudo -u swingset swingset storage plan`; the reviewer reads the counts
   and the digest; then `sudo -u swingset swingset storage apply <digest>`.
   Apply reuses the written plan's day boundary, so it may cross midnight.
   Receipt, with its `room`.
5. `sudo -u swingset swingset gc --collect --plan`, review,
   `sudo -u swingset swingset gc --collect --apply <digest>`. Receipt.
6. `sudo swingset-disk`, then `sudo -u swingset swingset gc --reclaim`; it
   refuses without `2.5 × bytes in use + 12 GB` on the quota. Record the
   size before and after and the peak quota used. If it refuses, end the
   hold anyway: the cap reads the file, new rows reuse the 35 GB of free
   pages, and a segmented set sizes its room by bytes in use, so backups
   continue. Reclaim is then a separate step after step 9's checkpoint
   removal, which is where its room comes from, so it needs switch 2b and
   a drill that passed: its own hold (`operator-hold`, then the writer and
   control locks it takes), sized again at `2.5 × bytes in use + 12 GB`.
   Until it runs the file stays about 73 GB and D-0233's 76 GB cap stays.
7. End the hold. Watch phase 3's drain and first build per `AGENTS.md` and
   phase 3's steps 4 to 6.
8. Set `backupSegmented = true` (a `nix/`-only switch, no re-queue). Start
   `swingset-backup.service` by hand (a local set), then
   `swingset-maintain.service`: its drill runs, since no set has passed one.
   Record the set and the drill.
9. Post-check receipt after 24 hours: the file size and its `dbstat` top
   10, `objects/` bytes by class, growth per day, the set sizes, the drill
   result, page-ins and evictions per day, `[incomplete-generations]`
   quiet, and phase 3's post-check items. Then: a record lowering
   `max_database_bytes` from D-0233's 76 GB (proposal: the size after
   reclaim plus 8 GB, only after reclaim has run); removal of the old
   `run_*` and named checkpoints once a set has passed its drill
   (independent check), then reclaim under its own hold if step 6 waited;
   and, if the owner agrees, `backupUpload = true` (D-0232). Upload goes to
   a new private dataset, `skeswa/swingset-backup-sets`, which the owner
   creates first and the token can write; never to `skeswa/swingset-archive`,
   whose checkpoint history `restore --archive-commit` reads and a squash
   would delete (D-0234). Watch the first upload.
10. Acceptance of record over a month: the file under 15 GB, or the gap
    attributed and moved to phase 5 or open decision 4; a daily set and a
    weekly drill passing on the worker; `objects/` within 80 GB.

Rollback: before step 3's migrate, the previous system generation. After
it, only step 2's byte copy: phase 2 code refuses schemas 35 and 36, and
after step 4 nothing older reads packs. Work since the hold is lost and
re-fetched. Phase 3's `[release] path = "whole"` stays the answer to an
assembly bug.

Cost the operator faces (Mac timings from the closing rehearsal on a
loaded Mac; the worker is slower, **unverified**): a hold of about 3 to 4
hours: the byte copy of a 73 GB file to the Mac with its sha256 and
`quick_check`, migrate (about 6 minutes for 34 → 36), storage plan (19 to 21 minutes) and
apply (25 minutes, its own 7-minute plan included), collection (5 minutes),
reclaim when it has room (17 minutes, about 2 times the bytes in use at
peak), and the reviewer's reading between them. Peak disk: the move's copy of the kept references (a few GB
with `secure_delete` off), then reclaim's `2.5 × bytes in use + 12 GB`. After
the hold: phase 3's drain (every project and link unit, 2.3 to 2.8 hours on
the Mac, all cut off but phase 3's new units) and a cold build of about 6
to 8 minutes; about 0.5 GB of objects; the file from about 73 GB to about
32 to 40 GB (W1); a first set of about 4 minutes (a 3.3 GB part and a
1.4 GB segment) and a drill of about 3 minutes; the independent check.
After it: a daily local set of a 3 to 4 GB part plus the day's new files
(two sets kept, about 8 GB with the shared segment); a weekly drill that
needs the database's size, the set's files and 20 GB free under `/var/tmp`;
maintain's bounded eviction and packing at 05:30; the cap lowered by its own
record.

Known limits after phase 4:

- The file stays about 32 GB until open decision 4 or phase 5 (W1).
- Equivalences and `derivation_recipes` are not collected (the rest of
  invalidation step 3); the `[equivalences]` line at 500,000 rows stands.
- Nothing in the code revokes a generation today, and the chain a
  revocation reads is protected (F35). One that met a packed body would
  raise `KeyError` and roll back; `body_packs.check_revocation` names the
  body for `storage restore-body`.
- A reuse of a large evicted generation pages it in over several ticks;
  until it is whole the staged run stops with `SupersededWorkError`.
- A daily set carries the whole compressed database (W6), and each weekly
  drill needs the database's size, the files and 20 GB free.
- Upload stays off until the owner turns it on and creates the sets
  repository; the Hub's per-commit and storage limits are **unverified**,
  and its collector and history squash (D-0234) run only against a fake
  Hub offline.
- A lost object comes back only from a local set that holds it (or an
  older set copied in by hand), or, for a pack, when a unit reuses that
  generation (`output_packs.rebuild`); until then its readers fail closed
  and the evict guard holds.
- `VACUUM INTO` holds a read snapshot for minutes (129 s on the Mac at
  31.6 GB), so the WAL grows during a backup and `[wal-large]` may fire.
- `identity_link_history` (1.68 GB) and `execution_*` (1.70 GB, phase 5)
  are untouched.
- A derive tick (2.3 to 2.5 GB) and doctor (4.4 GB) exceed the 1.5 GB step
  budget, as before phase 4.

**Phase 5: removals (about 2 weeks, one subsystem per switch).** The table
in section 8, each behind its replacement's receipts, with tests deleted in
the same change. It also removes the cycle, folds `pending_work`,
`work_generations` and the requirement tables into `work_queue` (F14), and
shrinks operator pauses to loop, host and source scopes (deferred from
phase 2). The `event_*` dependency report also covers the writers
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
  under-declares a dependency and a stale link survives. Mitigation, built
  in phase 3: a SQLite authorizer sees every read of a unit body whatever
  module issues it; the fixtures run armed with zero undeclared reads and a
  static test keeps `project/` and `link/` behind the unit entry points; a
  weekly `verify` in `maintain` re-derives a 1% sample and alarms on a
  mismatch. The authorizer's blind spots remain in production (F25).
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
