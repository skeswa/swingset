# Target design: Postgres holds everything, Dokploy runs it

This page describes where Swingset ends up. [The stages](stages.md) say how
to get there safely. Words in **bold** on first use are defined in the
[plan's glossary](README.md#words-used-in-this-plan). Measured facts come
from the [2026-10-19 survey](../../../journal/investigations/2026/platform-survey-2026-10-19.md).

## The end state in brief

One Postgres database holds everything Swingset knows and everything people
or agents decide. That covers fetched pages, parsed evidence, derived
results, releases, overrides, approvals, holds and settings. Large files
that never change live in **object storage**, named by their sha256 hash,
and Postgres records each one. A few worker services on the owner's server
run the five loops (acquire, plan, derive, release, maintain). The release
builder still writes the Hugging Face files, exactly as today. A **catalog**
inside Postgres keeps every published release in a form that is easy to
query, so a public API can be built on it later.

## Design rules

1. **Keep today's behaviour first.** Every table moves to Postgres with the
   same columns and the same types of values (text, whole numbers, decimals).
   No column changes to a date, true/false or JSON type during the move.
   Better types come later, one at a time, with tests.
2. **Never change a hash by accident.** Many ids and checks are hashes of
   stored text or of whole rows. The Postgres reader must hand the code the
   same Python values SQLite does (`str`, `int`, `float`, `None`). Tests pin
   "golden" hashes taken from real production rows. A hash formula changes
   only as a planned production switch with receipts, because stored
   approvals (year acceptance, contract reviews) are checked against those
   hashes.
3. **One writer at a time, as today.** SQLite lets one transaction write at
   a time. Postgres does not, so the port adds the locks the
   [database port page](database.md) calls A, B and C (section 5.2). Every
   write transaction takes lock C first. Every read transaction sees one
   fixed snapshot of the data (`REPEATABLE READ READ ONLY`). Relaxing this
   comes later, one subsystem at a time.
4. **Every decision is a row with an author, a time and evidence.** Rules
   that are code (admission contracts, the identity policy, linker weights)
   stay code. Each release row records the hash of the code it ran with.
5. **Published facts keep stable ids and a release.** A renamed id leaves a
   redirect. A suppressed person disappears everywhere, including history.
6. **Only one machine fetches.** Two fetchers would break the request limits
   we promise each site. A test run is built so it cannot fetch or publish,
   not just told not to.

## Database layout

Postgres groups tables into **namespaces** (Postgres calls them schemas;
this plan says namespace to avoid confusion with "schema 37", which is a
migration number).

| Namespace  | Holds                                                                                              | Written by                                |
| ---------- | -------------------------------------------------------------------------------------------------- | ----------------------------------------- |
| `fetch`    | hosts, budgets, watches, robots, snapshots, request records                                        | acquire, plan                             |
| `evidence` | source units and generations, admission decisions, observations, findings                          | derive                                    |
| `derive`   | derivation scopes, generations, payloads, dependency sets, work queue                              | derive                                    |
| `project`  | today's projection tables: events, contests, rounds, entries, placements, marks, dancers, identity | derive                                    |
| `release`  | partitions, candidates, manifests, verdicts, publications, the baseline pointer                    | release                                   |
| `ops`      | runs, alerts, stored-object records, backup records, migrations                                    | every loop                                |
| `gov`      | overrides, approvals, proposals, holds, settings (new)                                             | people and agents, through `swingset gov` |
| `catalog`  | published releases in queryable form (new)                                                         | release                                   |

**Database roles** limit who can write what. The worker role writes the
pipeline namespaces. Each person or agent label that changes decisions has
its own login role for `gov`; none of those passwords sit on the worker.
A reader role (later the API's) reads `catalog` views and a small set of
evidence views (sources, snapshots, findings), never page bodies, notes or
suppressed people. Schema changes run as a separate job with the owner role.

### Porting today's tables

In short: SQLite and Postgres sort, count and lock in slightly different
ways, and each difference could change a hash or let two writes collide.
The survey counted 142 tables, 809 triggers and 1,787 places in the code
that run SQL. The [database port page](database.md) has the detailed rules;
these are the choices most likely to go wrong.

- **Sorting:** the database is created with the byte-order `C` collation, and
  every query that sorts nullable columns says `NULLS FIRST` or
  `NULLS LAST`. SQLite sorts empty values first; Postgres sorts them last.
- **Publication fence:** today 451 triggers block writes while a release is
  being published, some only for certain rows. Generate the same triggers
  in Postgres's trigger language (PL/pgSQL), row by row, from the same rule
  list (`publication_fence.py`, `NARROW`). Keep the five-table control
  exception, and require lock C.
- **Change counters** such as `history_dispatch_fence.revision` stay as rows
  updated inside the transaction. A Postgres sequence (a built-in counter) is
  not safe here: its next value is visible before the transaction commits.
- **Trigger-built keys:** triggers that build JSON keys must write compact
  JSON (`["calendar","x"]`), the same text Python looks up. Each such trigger
  gets a test.
- **Row numbers:** SQLite gives every row a hidden number (`rowid`), and some
  code uses it to remember where a scan stopped. These become ordinary
  numbered columns, filled from the old numbers during import. Code that
  asks for the number of a new row uses `RETURNING` instead.
- **Large manifests** (up to 259 MB) need a measured choice between `text`
  and `bytea` storage, because reading part of a compressed value
  decompresses all of it. The port page plans `bytea`, which conflicts
  with rule 1; stage 1 measures and decides.
- **No extra JSON columns** on the big tables. Where a query needs JSON
  fields, use a view or an index on an expression.
- **Size:** plan for about 40 GB, the same as today. An earlier idea to
  delete 17.7 GB of "unnamed" dependency sets before the import was wrong:
  the weekly collector already keeps only live sets.
- **Space after big rewrites:** a full re-derivation rewrites most
  dependency lists, which leaves old copies that Postgres must clean up
  (`VACUUM`). Long read snapshots, such as an hour-long release build, delay
  that cleanup. Plan a cleanup (`pg_repack`) after each full re-derivation,
  and end idle transactions automatically
  (`idle_in_transaction_session_timeout`).

### Decisions as data (`gov`)

Each override file and each approval becomes a table. A row is never edited.
Changing a decision adds a new row that **supersedes** the old one. Every
row has: who proposed it, who approved it, when it was proposed, when it
takes effect, the evidence (a decision record, review or receipt), and the
database user that wrote it.

| Today                                                                                                                                   | Becomes                                                                   |
| --------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------- |
| `overrides/*.csv` (eight files)                                                                                                         | one `gov` table each                                                      |
| policy settings in `sources.toml` and `hosts.toml` (enabled sources, the history start date, the size cap, request spacing and budgets) | `gov.settings`, `gov.host_policy`                                         |
| the publish switch (`dryRun` in Nix)                                                                                                    | `gov.settings`                                                            |
| the `operator-hold` file and retention hold files                                                                                       | `gov.holds` (plus the out-of-band hold below)                             |
| contract policies and reviews, pauses, year acceptance                                                                                  | stay as they are at first; move into `gov` later with their own hash plan |
| who asked for a requeue                                                                                                                 | `gov.requeues`                                                            |

- **Proposers and approvers are different people or agents.** A trigger
  records the database role that wrote each row, and an approval only counts
  if its role differs from the proposal's. The owner may approve their own
  proposal with an explicit flag, except for suppressions and turning
  publishing on, which always need a second approver (AGENTS.md's
  independent check).
- **Identity decisions can be superseded but never retired,** because their
  journal is append-only and its hash is published.
- **Hashes for recipes:** each table's input hash covers only the columns
  that change results (not author or notes), in an explicit `position`
  order. Moving from files to tables changes these hashes once. All eight
  override tables move in one deploy, so a recorded old-to-new map makes
  that one planned re-run of their consumers.
- **History is imported honestly** from the 84 captured input bundles, the
  versions production actually used, with the bundle's acceptance time.
  Rows are marked `created_by = import`. Git commits are evidence only:
  every override commit shows the owner as author, even when an agent wrote
  the change.
- **Tuning that changes no output** (loop schedules, memory and batch sizes)
  stays in config files deployed with the code.

### The published catalog (`catalog`)

- **The builder stays the only writer of Hugging Face files.** Today's
  release depends on byte-level details: row order, row groups, parquet
  types, the changelog merge and hashes of the file bytes. Rewriting that
  from Postgres would risk divergence. Instead, after each publication the
  catalog loads the published parquet files, and a check confirms their
  hashes.
- **Releases:** `catalog.releases` lists each release with a publication
  number. Numbers only go up, even when a release is a revert.
- **Versions:** each published row gets a version id and the range of
  publication numbers it was true for. Before choosing this layout, stage 1
  measures how many rows change between releases; columns like `run_id`
  change every time.
- **Redirects:** `catalog.id_redirects` handles every id level (events,
  contests, rounds, entries, judges) and one-to-many moves, derived from key
  differences between releases, with chains collapsed.
- **Suppression wins.** A new suppression removes the person from every
  catalog version and redirect, as a written exception to "rows are never
  edited". Catalog views also filter on current suppressions. A test checks
  that no suppressed id appears anywhere.
- **Search:** indexes on public keys, WSDC ids, dates and series, and
  indexes on names that also find near spellings (trigram indexes). Publishing `series` adds a table to the dataset, which is
  a schema change, planned as one.

### What the dataset may publish about decisions

Only an allowlist: series and event aliases, source URLs, nicknames and
approved release decreases, without author, note or evidence columns.
Suppressions and identity decisions are never published, because the
dataset never publishes private notes (`build/suppression.py`,
[data model](../../reference/data-model.md)). The `review_queue.suggested_override` column, today
"a CSV row for `overrides/`", changes meaning and is planned as a schema
change.

## Files in object storage

| Today                                        | In the bucket              |
| -------------------------------------------- | -------------------------- |
| `blobs/` raw page bodies                     | `bodies/<sha256>`          |
| `extracts/`                                  | `extracts/<sha256>`        |
| `objects/<class>/` packed bodies and outputs | `objects/<class>/<sha256>` |
| `partitions/` parquet cache                  | `partitions/<digest>/`     |
| `candidates/` release builds                 | `candidates/<id>/`         |
| `inputs/` captured input bundles             | `inputs/<digest>/`         |
| backups                                      | a separate bucket          |

- **Write once:** uploads ask the service to refuse an existing name
  (`If-None-Match: *`), so an object is never overwritten. Whether each
  provider supports this is **not yet verified**.
- **The row comes after the upload.** Postgres records an object only after
  the upload is confirmed. Local files stay until the bucket copy is checked.
- **A local cache** on the worker keeps parsing fast. It has a size limit.
  The bucket is the truth; every read checks the sha256.
- **Marker files become rows.** `BUILT` and `PUBLISHED` become a candidate's
  state; the `baseline` symlink becomes a row.
- **A second copy** always lives with a second provider, made by a separate
  copy job that uses its own keys. The worker cannot delete it. Object
  versions alone do not protect the data, because a key that can delete
  versions can still remove them. Cloudflare R2 has no object versioning or
  bucket replication (checked 2026-10-19).

## Backups and recovery

- **Targets:** lose at most 15 minutes of data (recovery point) and be
  running again within 4 hours (recovery time). Stage 3 times a full
  restore to check this.
- **Postgres:** continuous **WAL archiving** to its own bucket and keys, with
  **point-in-time recovery**, using a custom Postgres image (pgBackRest or
  WAL-G). The log is saved at least every 15 minutes (`archive_timeout`), or
  the recovery point cannot be met. Dokploy's scheduled `pg_dump` stays as a
  second copy.
- **Weekly drill:** restore to a scratch database at a chosen time; check
  that every recorded object exists with its hash.
- **The Mac copy and the 45 GB of local checkpoints** retire after the
  switch, once an independent check approves deleting them.

## Running on Dokploy

- **Services, not one container.** Two or three Dokploy services, each with
  its own memory limit: acquire and plan; derive; release and maintain.
  Today's per-loop limits add up to about 17 GB, and a backup run used
  8.5 GB, counting cached file pages
  ([survey](../../../journal/investigations/2026/platform-survey-2026-10-19.md)). One container would let a single out-of-memory kill stop every
  loop.
- **Each service runs a small scheduler.** It keeps today's calendars and
  budgets, stops a tick that runs over (first asking it to stop, then forcing
  it after the stop margin), and runs a missed tick once, not many times. Dokploy's own cron is
  not used: it has no overlap lock or timeout, and v0.30.8 had open bugs
  where jobs never ran.
- **Locks live on the writing connection.** If that connection drops, the
  tick stops. No connection pooler sits in between, because poolers break
  these locks. A test kills the connection mid-tick.
- **An out-of-band hold.** A hold flag in each service's environment (or a
  file on its volume) is checked before the scheduler connects to Postgres.
  Restores and imports always start with it set. The `gov.holds` row is the
  normal hold; the flag works even when Postgres is down or being restored.
- **Alerts leave the server.** An outside dead-man's switch (a service that
  alarms when pings stop) is pinged every tick. Alerts are sent before they
  are written to Postgres. New alerts cover failed WAL archiving, the WAL
  folder growing, and container restarts.
- **Time:** all code uses Python's clock or `clock_timestamp()`, not
  `now()`, and every container runs in UTC.
- **Network:** no public Postgres port; the owner and agents reach it through
  an SSH tunnel. Postgres sits on its own network that other services on the
  server cannot reach. No public domain on the health endpoint. The Hugging
  Face token is limited to the one dataset. Dokploy's own login uses
  two-factor login and an address allowlist, and Dokploy is kept updated,
  because it holds every secret.
- **Connections:** each service holds a few connections at most, well under
  Postgres's limit, with no pooler (see locks above).
- **Deploys:** a new image under a hold, then migrations, then the hold
  lifts. Migrations add before they remove, so the previous image still
  runs if a deploy is rolled back.
- **Disk budget** on the shared 800 GB disk: Postgres and its WAL, the local
  cache, build spill, the drill's scratch database (about 62 GB today), and
  the other services already there. Stage 3 writes the budget down.

## What does not change

The pipeline itself: sources, parsers, contracts, the five loops, the gate,
independent checks before publishing, the fetching rules and budgets, and
the decision records. The move changes where state lives and what runs it.
