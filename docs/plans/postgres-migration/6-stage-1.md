# 6. Stage 1: port to Postgres

Read [5. Stage 0](5-stage-0.md) first; next is [7. Stage 2](7-stage-2.md).

**Goal:** the pipeline, with stage 0, runs on Postgres and passes every test
on a sampled import. Production does not change: the port lives on its own
bookmark, rebased on `main`, and merges at the switch.

**Where it runs:** the Mac, with Postgres in Docker and a sampled import of
a few thousand units. Full-size work happens on the server in the dry run,
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

- **Prepare the Mac:** stop other projects' containers; give OrbStack about
  24 GiB in total (today 16, of which production may use 8; if changing it
  restarts OrbStack, do it under a production hold); keep the Mac awake.
  Stop local work whenever the Mac's free disk falls below 80 GB, which
  leaves room for production's restore drill.
- **Postgres 17,** the version on Rhuidean (17.11, checked 2026-10-19), on
  the Mac too.

## Steps

1. **Port the SQL** ([3. Database code](3-database.md)): the engine core,
   then subsystem by subsystem, following the value rules. Review the 23
   direct `sqlite3.connect` calls and the 120 queries built from strings.
   Start with one parse unit and one dancer projection, end to end.
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
     row appears once; ids and hashes are unchanged; budgets, next-allowed
     times, admissions, pauses, cursors, the identity journal position,
     selected generations, the baseline and unfinished runs match; every
     file a row points to exists. The worker role gets access only after
     the audit passes. A failed import starts again in a fresh database.
5. **Stage 0's code** ([5. Stage 0](5-stage-0.md)), on the same bookmark.
   **Label every table** irreplaceable or rebuildable, in one list, with a
   test that fails on an unlabelled table, and **the backup job** for the
   irreplaceable tables and the files
   ([4. Hosting](4-hosting.md#backups-and-recovery)). Measure its size on
   the sampled import, and rebuild the sample from it.
6. **Tests on Postgres:**
   - the suite, each test in its own database (advisory locks are per
     database); concurrency and crash tests with real commits in separate
     processes; no network, no Hugging Face token;
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
     acknowledgement, recovery from a pending candidate. No test is deleted
     or weakened because the engine changed;
   - **the loops on the sampled import,** with fetching off, then a verify
     run and a re-key on it.

## Gate 1

- the suite passes on Postgres, with golden hashes equal;
- the schema parity check passes;
- the sampled import's audit passes;
- a verify run on the sampled import is clean, and a re-key leaves every
  unit current (gate 0);
- every table is labelled, and the sample rebuilds from its backup to the
  same release.

Local code needs tests and formatting only (AGENTS.md); no independent
check.

**Rollback:** nothing to roll back; production never changed.

Next: [7. Stage 2](7-stage-2.md).
