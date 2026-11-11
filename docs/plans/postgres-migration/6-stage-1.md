# 6. Stage 1: port to Postgres

Read [5. Stage 0](5-stage-0.md) first; next is [7. Stage 2](7-stage-2.md).

Status: implemented and Gate 1 passed locally on `postgres-migration`,
2026-11-05. Native Postgres 17.11 passes schema parity, golden hashes,
the complete regression suite and the sampled import's typed audit.
Graph verification and re-key close Gate 0. The sample rebuilt from its
actual prepublication backup into an empty database and volume, reproducing
all 21 release files and passing the full post-rebuild history audit.
The maintained developer loop meets its feedback and core budgets.
See the [outcome and receipts](../../../journal/investigations/2026/stage-1-implementation-2026-11-04.md).

**Goal:** the pipeline, with stage 0, runs on Postgres and passes every test
on a sampled import. Production does not change: the port lives on its own
bookmark, rebased on `main`, and merges at the switch.

**Where it runs:** the Mac, with tests run by mise's Python against a
Postgres 17 container, and a sampled import of a few thousand units. The test setup this
stage builds is the one development keeps after the switch
([10. Local development](10-local-development.md#tests)). Full-size work happens on the server in the dry run,
because its initial 138 GiB of free disk was shared with the production VM.

November 13 policy update: the owner raises the complete core gate from 180
to 300 seconds (five minutes), including setup, verification and cleanup.
Summary format `swingset-core-run-v2` records `budget_seconds` and
`within_budget`; earlier reports keep their original budget and outcome.
This changes the feedback target, not test selection, resource caps or worker
write deadlines. The older measurement history below remains historical.

## Before it starts

- **Check the server, read-only.** The whole plan depends on it, and nobody
  has measured it since 2026-09-14:
  - free disk and inodes today (the host is AMD64, 24 cores, 62 GiB of
    memory and a 916 GB disk, per Rhuidean's platform reference);
  - what else runs there;
  - Dokploy's version and login safety;
  - whether other containers can reach a database on Dokploy's shared
    network;
  - whether the Postgres disk supports reflinks (XFS or btrfs), for cheap
    verify scratch copies.

  The disk budget is about 420 GB during the move (the old formula: max(200
  GiB, 8 × database + 3 × files + 50 GiB), with 20% of space and inodes
  free). The dry run replaces it with measured needs plus 30%. If the
  server cannot give this, the plan changes before any code does.

- **Prepare the Mac:** keep it awake; stop other projects' containers.
  Postgres runs in OrbStack beside the production VM, under OrbStack's
  16 GiB cap, of which production may use 8: each test container is
  limited to 2 GiB, and the dev database for the sampled import to 4. Stop
  local work whenever the Mac's free disk falls below 80 GB, which leaves
  room for production's restore drill.
- **Postgres 17,** the version on Rhuidean (17.11, checked 2026-10-19):
  match its patch release, extensions, locale and relevant settings.
  Pin the native ARM64 image for the Mac and the production AMD64 image
  for CI; the server's proofs use its exact production digest.
- **No Nix on the bookmark:** mise pins Python, uv and node; the bookmark's
  CI builds the image and tests inside it. `main` keeps Nix until the
  switch, because production deploys from it.

## Measured test-loop work

The maintained developer gate passed on the final application source:
2,583 cases in 179.39 seconds internally and 179.50 for the complete command.
All selections, assignments, executions, source identities and cleanup checks
passed. Cached readiness took 0.61 seconds; the slowest of three warm startup,
admission, projection and replay samples was 0.53, 0.82, 0.45 and 2.34 seconds
respectively
([current receipt](../../../journal/evidence/runtime/postgres-stage1-2026-11-04/developer-current.json)).
An earlier physical-profile check created that absent service in 3.75 seconds
with TEST, DEV, seed and dependencies cached
([original receipt](../../../journal/evidence/runtime/postgres-stage1-2026-11-04/developer-final.json)).
The current readiness measurement reuses it and is not a cold-start measurement.

Bare `mise run test` first runs the unchanged 30 ms recovery case in one
isolated process, then the remaining cases in four four-worker clients.
Every phase reports the full selection; assigned and executed cases must form
its exact disjoint union. A failed prologue stops the run. Setup, validation,
reporting and cleanup remain inside the 300-second gate. File-cost hints
change ordering only and update only after complete verified success.

The ordinary core uses a separate warm durable `physical` profile, capped at
2 GiB and two CPUs. Its exact container, volume, settings, roles, instance and
known-fresh database inventory must match before and after use. PID1 reaps
only its own copies on both test endpoints. Unknown state retains its owner
records and blocks reuse. Lifecycle fault tests, native full runs and CI keep
fresh clusters. Process polling preserves resource-sample cadence while
returning promptly after completion. The Mac's 7 GiB combined working-memory
stop and 80 GiB free-disk floor remain unchanged.

No selected test, timeout, memory limit or durability check was reduced.
The [measurement history](../../../journal/investigations/2026/stage-1-test-loop-measurements-2026-11-05.md)
preserves the triggers and failed candidates, including the unsuccessful
ordering-only repeats. First-time downloads are outside the cached readiness
measurement; missing-service startup still counts when a core command needs it.

## Steps

1. **Port the SQL** ([3. Database code](3-database.md)): the engine core,
   then subsystem by subsystem, following the value rules. Review the 23
   direct `sqlite3.connect` calls and the 120 queries built from strings.
   Start with one parse unit and one dancer projection, end to end.
   Measure a 256 MiB manifest through 64 KiB byte windows: client RSS must
   stay below 128 MiB and the read below 15 seconds. A failed measured run
   is the trigger to change the native large-value reader or TOAST storage;
   retain the measurement before changing it.
   Build the warm test service, focused/watch commands, bounded seed and
   local replay with that first slice, so the rest of the port uses them
   ([development loop](10-local-development.md#the-everyday-loop)).
2. **Pin golden hashes.** Take real rows from production and record the
   hashes today's code computes over them: inventory, corpus, selection,
   policies, generation ids, payloads. Tests require the port to give the
   same hashes.
3. **The schema in Postgres:** one starting migration that creates schema
   37's tables in their namespaces ([2. Schema](2-schema.md)), with the
   triggers, locks and roles of [3. Database code](3-database.md).
   **Schema parity check:** generate a manifest of SQLite schema 37 and
   compare: tables, columns in order, declared types, defaults,
   nullability, keys, indexes, foreign keys with their actions, and
   triggers. Equal counts are not parity. If production adds a migration
   before the switch, the port adds its Postgres form.
4. **The importer,** tested here on a sampled import:
   The bounded local sample starts with 2,500 units, at most 1 GiB of
   database rows and 2 GiB of files, and a 540-second read transaction.
   Exceeding a bound or repeatedly scanning whole source tables stops the
   sample. A measured scan receipt is the trigger to change that sampler's
   query plan before retrying; this does not authorize production tuning.
   - **Before loading:** check the checkpoint's digests
     (`restore_set.install`), open it read-only, run integrity and
     foreign-key checks, and match its schema and trigger fingerprint.
     Reject, never coerce: NUL bytes or invalid UTF-8 in text, a value whose
     storage class does not fit its column, NULL in a key, an out-of-range
     integer, a non-finite float where none is allowed, bad JSON, a value
     over Postgres's 1 GB field limit.
   - **Loading:** create tables with their keys, then COPY the rows,
     streaming large values. Then add triggers, other indexes and foreign
     keys, validate every constraint, grant rights, run ANALYZE, and compare
     the catalog with the schema manifest. Never bypass triggers on a
     database that will go live. Each table commits with its receipt; a
     killed table restarts from its beginning. Row numbers and sequences
     follow [3. Database code](3-database.md#row-numbers-and-sequences).
   - **The audit,** before anything else runs (no `recover_admissions`, no
     migration, no acceptance): read each side with its own reader, sort
     rows by key in byte order, encode each field as a type tag, an 8-byte
     length and its bytes, and hash each table with SHA-256. Every source
     row appears once, except the explicitly created destination
     environment identity ([page 10](10-local-development.md#dev-and-production-stay-apart));
     ids and hashes are unchanged; budgets, next-allowed
     times, admissions, pauses, cursors, the identity journal position,
     selected generations, the baseline and unfinished runs match; every
     file a row points to exists. The worker role gets access only after
     the audit passes. A failed import starts again in a fresh database.
5. **Stage 0's code** ([5. Stage 0](5-stage-0.md)), on the same bookmark.
   **Give every table a class** (history, rebuilt or bookkeeping), with a
   written row rule where a table mixes them and a reset rule for each
   bookkeeping table, in one list. List mutable anchor fields separately:
   restore all fields exactly, then permit only those normal transitions
   during rebuilding. Add a test that fails on a table with
   no class; **list every reference** out of a history row, followed
   transitively, with a test that fails on one that leaves history; and port **the backup job**
   from today's snapshot protocol for the history rows, the files and the
   publication metadata, including the backup each publication takes
   first ([4. Hosting](4-hosting.md#backups-and-recovery)). Measure its size on
   the sampled import, and rebuild the sample from it.
6. **Tests on Postgres:**
   - a warm local container for normal iteration, throwaway containers in
     CI and lifecycle fault tests, each test in its own database copied
     from a migrated template (advisory locks are per database), as in
     [10. Local development](10-local-development.md#tests);
     concurrency and crash tests with real commits in separate processes;
     no network, no Hugging Face token. Record the suite's time against
     today's, separating collection, setup, test bodies and teardown;
   - **developer loop:** measure cold and warm startup, focused tests,
     bounded replay and the unchanged core selection against page 10's
     budgets. A schema or fixture change invalidates the right template;
     a code edit reaches replay without an image rebuild or version bump;
     cancelled watch runs leave no live processes or reused test data;
   - **measured backup trigger:** the unchanged serial core exceeded 180 seconds
     before completing its selection. One instrumented checkpoint fixture made
     7,247 driver execute calls and 846 COPY calls, including two actual fresh
     restores, three full catalog parity passes and four runtime captures.
     Batch owner/grant statements and load catalog facts together while keeping
     every restore, closure audit and parity comparison. The profiled 6.92-second
     test body includes instrumentation overhead; it is not an unprofiled timing.
     See the [measurement receipt](../../../journal/evidence/runtime/postgres-stage1-2026-11-04/backup-profile.json).
   - **measured executor trigger:** the complete native Mac run with four
     workers selected 2,354 cases: 2,334 passed and 20 failed in 723.72 s.
     Setup totalled 542.27 worker-seconds and bodies 2,248.41; the armed
     subprocess alone took 256.24 s. CPU isolation ended after the 180-second
     budget had already failed. A matched 20-iteration probe, with identical
     source, query counts and outcomes, cut body time from 2.77 s on the Mac
     to 1.27 s on native ARM64 Linux beside Postgres. After the measured SQL
     and deadline changes, measure the complete unchanged core with eight
     Linux workers, a read-only checkout and 2 GiB per test container and
     Postgres. Preserve file affinity and all nested checks. Change the
     default executor only after the complete run meets 180 s within the
     resource limits; focused tests and replay stay native on the Mac.
     See the [baseline and probe receipt](../../../journal/evidence/runtime/postgres-stage1-2026-11-04/core-baseline.json).
   - **measured test database CPU choice:** four concurrent 20-iteration
     cohorts took 1.510 s with the test database capped at two CPUs and
     1.367 s with four, with identical source, outcomes and non-deadline
     query counts. Neither interval recorded quota throttling, so the 9.46%
     difference does not establish CPU-cap causality. Use four CPUs for the
     disposable test database while preserving its 2 GiB memory cap and
     1.5 GiB data tmpfs; development and lifecycle databases keep two CPUs.
     Reconcile an existing validated test container by exact ID without
     replacing it or its databases. The complete core must still meet 300 s;
     this bounded pair does not satisfy that gate. See the
     [CPU measurement](../../../journal/evidence/runtime/postgres-stage1-2026-11-04/test-postgres-cpu-profile.json).
   - **measured input writer trigger:** the complete core selected 2,411 cases
     and took 372.6 s; 2,384 passed and 27 failed. Setup, body and teardown
     totalled 2,268 worker-seconds, so ordering alone cannot meet 180 s with
     eight workers. On real persistent disk, three alternating fresh input
     captures fell from a median 2.276 s with one writer to 0.545 s with eight.
     Every capture produced identical 530-member bundles and manifests and
     still made all 1,062 real `fsync` calls. Validate existing members first
     on the caller thread, then use at most eight writers for missing members;
     wait for every writer before publishing the final manifest. A failed
     member must not publish a new manifest. Keep database and network work
     outside this pool, and measure the complete core again. See the
     [pre-change measurement](../../../journal/evidence/runtime/postgres-stage1-2026-11-04/input-member-writer-profile.json).
   - **measured memory trigger:** the first eight-worker Linux run reached
     the conservative 1.75 GiB client working-set margin and stopped after
     38.80 s, with 117 passes and two fixture cleanup errors. All eight
     workers collected the same 2,360 cases; the source stayed fixed and
     cleanup completed. Profile allocator, Arrow and thread retention before
     repeating that configuration. If the remedy is uncertain, measure two
     clients with four workers each, capped at 2 GiB per client, with a
     deterministic file partition applied after core selection. Verify that
     their node sets are disjoint and their union is the complete core;
     preserve every nested check and the 300-second gate. See the
     [resource receipt](../../../journal/evidence/runtime/postgres-stage1-2026-11-04/linux-core-memory.json).
   - **measured revision trigger:** two four-worker clients fit their strict
     2 GiB RAM-plus-swap limits, with peak working sets of 1.04 and 1.23 GB,
     zero OOMs and complete cleanup. Their empty revision setting repeatedly
     hashed the mounted source, and the incomplete run exceeded 180 s.
     A controlled probe returned identical `versions()` values while three
     fallback calls opened 990 files and read 12.14 MB in 48.92 ms, versus
     0.03 ms for the exact precomputed source identity. Compute that same
     digest once before each read-only job and verify the binding afterward;
     never reuse the image's old revision label. Repeat the complete core
     before changing the default workflow. See the
     [two-client receipt](../../../journal/evidence/runtime/postgres-stage1-2026-11-04/two-client-identity.json)
     and [controlled identity probe](../../../journal/evidence/runtime/postgres-stage1-2026-11-04/revision-identity-profile.json).
   - **remaining core trigger:** the exact revision pin preserved identity,
     but the two-client run still exceeded 180 s. Before its diagnostic stop,
     951 cases passed and two failed; both clients stayed below 1.33 GB working
     memory, with no swap or OOM and complete cleanup. A controlled 20-iteration
     Linux-local source snapshot was slower than the Mac bind (1.62 versus
     1.53 s), so that change is not justified. Profile the actual early core
     workers and armed subprocess before choosing another remedy; the default
     executor remains unchanged until the complete budget passes. See the
     [pinned-run receipt](../../../journal/evidence/runtime/postgres-stage1-2026-11-04/pinned-core.json).
   - **measured test filesystem trigger:** profiling the actual core prefix found
     39,727 real `fsync` calls taking 147.89 aggregate worker-seconds in a
     45-second diagnostic run. A controlled mandatory checkpoint and restore
     passed on both filesystems with the same 310 calls: 4.14 s on the volume
     and 3.56 s on tmpfs. Trial only the pytest payload root on a 768 MiB tmpfs
     within each client's unchanged 2 GiB RAM-plus-swap cap. Keep ownership
     ledgers on persistent storage, inherit pytest's failed-only retention in
     nested tests, export failure artifacts durably, and monitor bytes and
     inodes. Preserve every application durability call and every test. The
     complete core must still pass within 300 seconds before this becomes the
     default. See the
     [actual profile](../../../journal/evidence/runtime/postgres-stage1-2026-11-04/filesystem-and-core-profile.json),
     [controlled checkpoint pair](../../../journal/evidence/runtime/postgres-stage1-2026-11-04/checkpoint-filesystem-pair.json)
     and [cleanup review](../../../journal/evidence/runtime/postgres-stage1-2026-11-04/test-temp-cleanup-review.json).
   - **tmpfs trial failed:** the complete-selection trial stopped at 168.82 s
     after two actual kernel OOM kills in one client. Failed publication
     fixtures had retained extra states because their real 20 GB backup-space
     check correctly refused the 768 MiB filesystem; body packing and drill
     checks also require disk headroom. Both clients cleaned up their exact
     owners and durably exported failure artifacts. Keep the default executor
     unchanged. A narrower fixture placement must preserve the real space
     checks and stay within the same memory limit before another complete
     timing run. See the
     [resource receipt](../../../journal/evidence/runtime/postgres-stage1-2026-11-04/tmpfs-core-resource.json).
   - read tracking in armed mode through the planner
     ([3. Database code](3-database.md#read-tracking));
   - **lock and timeout tests:** the state lock holds across commits; B and
     C keep their order while a control waits; a busy loop skips its tick;
     a control that times out commits nothing; a dead process or killed
     connection frees its locks; the write limit fires for slow SQL, a lock
     wait and slow Python; a lost commit acknowledgement gives "uncertain"
     and no second outside effect;
   - **scenarios:** request budgets, Retry-After, a conditional 304, pause
     and resume, interrupted units, duplicate observations, identity
     correction and deletion, same-time release selection, a partial build,
     a publication reservation, a remote success with a lost
     acknowledgement, recovery from a pending candidate, the three Hub
     head cases at recovery;
   - **recovery order:** with baseline A and pending candidate B in the
     backup and B's push landed, recovery promotes B before downloading
     any Parquet, then downloads B's, and a build after it runs its gate
     against B; a baseline with a missing file blocks;
   - **backup by `COPY`:** a mixed table's history rule exports only its
     rows, all tables share one snapshot, and a restore passes every
     foreign key and the audit hashes; race reservation, acknowledgement
     and hold-file changes with snapshot capture, including crashes
     between database and file writes; restore an older backup using its
     retained image and rules, then migrate it forward;
   - **snapshots:** concurrent generation replacement leaves a multi-query
     reader on one version; a waiting writer sees the preceding commit
     after it acquires C;
   - **deploys:** a rollback across a migration is refused; failed and
     successful deploys preserve an existing operator hold and no old
     writer survives into the migration;
   - **environment guards:** fresh target identities survive import and
     restore; fixture loops work on `dev`, while real transports and
     production schedulers refuse it even without a shell flag;
   - **reservation, backup, push:** the full sequence; the backup lock busy
     (push skipped, reservation kept); the backup or its check failing (no
     push); the tick stopped or killed during the backup (no recorded
     backup, no push, the next tick cleans up); a crash after the backup
     and before the push; and no deadlock with the scheduled backup;
   - **existing coverage:** no test is deleted or weakened because the
     engine changed. The new `gov` tables and their chain tests belong to
     [the after-switch work](9-after-switch.md#decisions-into-gov), as
     [the schema plan](2-schema.md#the-rules-for-the-move) specifies;
   - **the loops on the sampled import,** with fetching off, then a verify
     run and a re-key on it.

Before its first render, a partitioned or whole build captures cutoff and run id in its immutable
recipe context and candidate policy. An unchanged complete context, recipe and
dependencies reuse those inputs during recovery, including after files became durable before
generation completion. Changed inputs capture fresh values. Coverage and
changelog must reproduce the same bytes without rounding time or changing the
public-health cadence ([D-0273](../../../journal/decisions/0273-capture-build-rendering-inputs.md)).

## Gate 1

- the suite passes on Postgres, with golden hashes equal;
- the stage 1 developer commands and seed are available; measured warm
  iteration and core times meet [page 10's budgets](10-local-development.md#the-everyday-loop);
- the schema parity check passes;
- the sampled import's audit passes;
- a verify run on the sampled import is clean, and a re-key leaves every
  unit current (gate 0);
- every table has a class, every reference is listed, and the sample
  rebuilds from its backup, into an empty database and volume, to the same
  release.

Local code needs tests and formatting only (AGENTS.md); no independent
check.

**Rollback:** nothing to roll back; production never changed.

Next: [7. Stage 2](7-stage-2.md).
