# What a move to Postgres and Dokploy must carry (2026-10-19)

**In short:** the database is 41 GB and almost all of it is internal
bookkeeping, not published data. It relies on about 800 SQLite triggers and
on one-writer-at-a-time behaviour. About 150,000 small files sit beside it.
Many decisions live outside the database. Porting the database code makes
production re-derive everything once. The [migration plan](../../../docs/plans/postgres-migration/README.md)
is built on these findings.

**Method:** four read-only surveys of the code and of production (schema 37).
Production was read with read-only connections and cheap commands. Table
sizes come from the owner's 2026-10-11 copy of the database (schema 36), so
they are close to today's file, not equal to it.

## The database

- **Size and shape:** 41.15 GB, of which about 4 GB is free pages. 37
  migrations, 142 tables, 1 view, 148 explicit indexes and **809 triggers**.
  1,127 columns: 914 TEXT, 193 INTEGER, 18 REAL; 67 are `*_json` text.
- **What fills it:** derivation and work-queue tables hold 28.6 GB. One
  table, `derivation_dependency_sets`, holds 18.5 GB of JSON lists of what
  each result depends on. Most of these lists are reached only indirectly,
  but they are still live: the weekly collector ran on 2026-10-13 and freed
  only 21.9 MB. Source generations hold 4.5 GB, the ledger 2.3 GB, identity
  2.2 GB, and the published tables only about 1 GB.
- **Triggers:** 451 publication-fence triggers generated over every table,
  189 history-timing revision bumps, 50 derivation triggers that maintain
  change tokens, about 60 immutability guards. 519 abort with `RAISE`.
- **SQLite-only features in use:** `json_extract`/`json_each`/`json_tree`
  (85 Python uses), `INSERT OR IGNORE`/`REPLACE` (97), `rowid` cursors (83),
  `julianday` (36), `blobopen` streaming, `VACUUM INTO`, `.backup()`,
  `immutable=1` reads, `set_progress_handler` write deadlines, 77
  `sqlite_master` lookups, deferred-FK "grant" rows that authorise deletes.
- **Access style:** raw SQL, no ORM: 1,787 `execute` calls (785 in `state/`),
  120 f-string statements, 23 direct `sqlite3.connect` calls (build spools
  and DuckDB scratch included).
- **Concurrency:** one writer at a time. File locks (`state.lock`,
  `heavy.lock`, `control.lock`, per-loop locks), `BEGIN IMMEDIATE` with a
  70 s busy timeout and a 60 s body deadline. Many read-then-write patterns
  are safe only because SQLite serialises writers.
- **Hashes over rows:** `payload_sha256` and manifest digests hash exact JSON
  text, so those columns must stay byte-identical text. Several digests
  (inventory, corpus, selection, policies) hash `SELECT *` tuples, so they
  depend on column order, Python value types and byte-order sorting.
- **Recipe identity:** the code scopes include `state/` and its migrations.
  The environment fingerprint (`recipe/env`) hashes every installed package
  and the Python binary itself. So porting the state code, adding a Postgres
  driver, or building a new Docker image makes every derived result out of
  date. Each such change means one full re-derivation: about 40k parse,
  33.5k project and 2.9k link units, which took 10 to 14 hours on the VM.

- **Further measurements (adversarial reviews, 2026-10-19):** the largest
  single dependency list is 259 MB. A backup run's memory peaked at 8.54 GB
  (systemd's count, which includes cached file pages). The weekly restore
  drill needed 61.6 GB of scratch space (production log, 2026-10-17). The
  weekly collector on 2026-10-13 freed only 21.9 MB, so nearly all stored
  dependency lists are live.

## Files beside the database

| Store                                                                 | Size          | Kind                                     |
| --------------------------------------------------------------------- | ------------- | ---------------------------------------- |
| `blobs/` raw bodies, gzipped, by sha256                               | 129 MB, 37.6k | authoritative, never deleted             |
| `extracts/`                                                           | 354 MB, 36.6k | rebuildable, kept                        |
| `objects/` packed parse bodies, derivation outputs, labels, manifests | 740 MB, 76.6k | authoritative once the row is stubbed    |
| `partitions/` stage-1 parquet cache                                   | 542 MB, 52.7k | cache                                    |
| `inputs/` captured config and override bundles                        | 952 MB, 84    | authoritative history of inputs          |
| `candidates/` release builds                                          | 13 GB, 114    | 67 published; `--revert-to` reads them   |
| `checkpoints/` sets, segments, old full copies                        | 45 GB         | backups; recovery still reads old copies |
| `runs/` tick records                                                  | 266 MB, 9.6k  | operational                              |
| `.cache/huggingface`                                                  | 5.4 GB        | unbounded download cache                 |
| `operations/` one-off scripts                                         | 3.1 GB        | scratch the runtime never reads          |

About 150k small, write-once, content-addressed files (1.7 GB) are what
object storage would hold. Marker files (`BUILT`, `PUBLISHED`), the
`baseline` symlink, `operator-hold` and lock files carry program logic today.
There is no off-machine backup: uploads are off and the owner copies the
database to the Mac by hand.

## Decisions that live outside the database

- **Override files** (git and 84 captured bundles only): `series_aliases`
  644 rows, `finding_reviews` 86, `nicknames` 119, `source_urls` 56,
  `event_aliases` 2, `release_decreases` 1, `identity_overrides` 0 (mirrored
  into `identity_decisions` when accepted), `suppressions` 0. Each is a
  recipe input; a change re-runs its consumers.
- **Config** (`sources.toml`, `hosts.toml`): mixes policy (enabled sources,
  `history_start`, the 50 GB cap, host spacing and budgets) with tuning.
- **In the database already, with actor and time:** contract policies and
  reviews, operator pauses (append-only), year acceptance (overwritten on
  re-accept), identity decisions.
- **Elsewhere:** the maintenance hold is a file; retention holds are JSON
  files; requeue authors sit inside a reason string; the publish switch is a
  Nix setting; release verdicts live only in the candidate folder.

## The published model and an API

The internal projection tables (events, contests, rounds, entries,
placements, callbacks, marks, dancers, registry placements, link candidates,
coverage) already match the published columns, minus two build-time scope
columns. Identity links, the review queue, the changelog and snapshots are
assembled only at build time. Ids come from natural keys, but a merge renames
an event (D-0253 to D-0262) with no redirect table; rows carry no release id;
the `series` table is not published.

## Runtime and Dokploy

- **Today:** five systemd loops (acquire and derive every 5 min, plan every
  30, release hourly, maintain daily) plus backup, watchdog, summary and
  scratch-clean timers on a NixOS VM (4 CPUs, 8 GiB, 275 GB quota) inside
  OrbStack on the owner's Mac. Measured peaks: release 2.2 GB RSS, derive
  1.0 GB. The Mac sleeping has stalled production for 8 to 48 hours at a time.
- **Server:** the owner's server at sandile.dev (8-core Xeon, 60 GB RAM,
  800 GB disk; from the 2026-09-14 hosting investigation, not re-verified).
- **Dokploy** (v0.30.8, from its docs and GitHub):
  - It runs apps as Docker Swarm services or Compose stacks.
  - Its Postgres template (default `postgres:18`) backs up only with
    `pg_dump` to S3. It has no continuous log archiving or point-in-time
    recovery.
  - Volume backups are full copies staged on local disk.
  - Scheduled jobs have no overlap lock or timeout. Open bugs report jobs
    that never run (#5530, #5139, #5403).
  - Notifications cover deploys and backups, not failed jobs or memory kills.
  - Its REST API uses an `x-api-key` header. Memory limits must be given in
    bytes (#4927).
  - We did not check how outgoing traffic is addressed.
- **Object storage:** Cloudflare R2 does not support object versioning or
  bucket replication (its S3 compatibility page, checked 2026-10-19).
