# 6. Stage 1: port to Postgres

Read [5. Stage 0](5-stage-0.md) first; next is [7. Stage 2](7-stage-2.md).

**Goal:** the pipeline, with stage 0, runs on Postgres and passes every test
on a sampled import. Production does not change: the port lives on its own
bookmark, rebased on `main`, and merges at the switch.

**Where it runs:** the Mac, with tests run by mise's Python against a
Postgres 17 container, and a sampled import of a few thousand units. The test setup this
stage builds is the one development keeps after the switch
([10. Local development](10-local-development.md#tests)). Full-size work happens on the server in the dry run,
because the Mac has 138 GiB of free disk shared with the production VM.

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

## Steps

1. **Port the SQL** ([3. Database code](3-database.md)): the engine core,
   then subsystem by subsystem, following the value rules. Review the 23
   direct `sqlite3.connect` calls and the 120 queries built from strings.
   Start with one parse unit and one dancer projection, end to end.
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
   bookkeeping table, in one list, with a test that fails on a table with
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
   - **`gov` chains:** two approved successors for one row refused;
     A → B → C with B and C waiting, where withdrawing B is refused and
     withdrawing C then B succeeds; withdrawing an active proposal
     refused. No test is deleted
     or weakened because the engine changed;
   - **the loops on the sampled import,** with fetching off, then a verify
     run and a re-key on it.

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
