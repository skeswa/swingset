# Move to Postgres and Dokploy

Swingset moves off the owner's Mac and onto the owner's server. Postgres
becomes the one place that holds all of Swingset's data and decisions,
including overrides, exceptions and approvals. Large files move to object
storage, and Dokploy runs the worker. The database is built to be queried,
so it can back a public API later. Hugging Face keeps getting the same
full releases.

Status: design, revised 2026-10-19 after five adversarial reviews
([D-0263](../../../journal/decisions/0263-postgres-holds-all-state-and-moves-in-four-stages.md)),
with a stage 0 added the same day so that code and environment changes stop
forcing re-derivation
([D-0264](../../../journal/decisions/0264-declared-transform-versions-and-differential-proofs-decide-re-derivation.md)).
Not started. Production stays on SQLite on the Mac until stage 4.

## Why move

- **The Mac sleeping stops production.** Collection has stalled for hours
  at a time, and on 2026-10-17 a release check stalled for two days.
- **Decisions are scattered.** Overrides are files read from the Mac,
  approvals sit in free-text fields, and a hold is a marker file. None of
  them can be queried, and their history is only in git.
- **There is no backup off the machine.**
- **The data is worth building on.** A queryable store with stable ids and
  release history is the base for an API.

## The plan in five stages

0. **Prove changes instead of re-deriving:** a result goes out of date only
   when its transform's declared version or an input changes. A differential
   proof on a copy decides, before each deploy, whether a code or environment
   change needs a new version. Without this, the move would re-derive
   everything three times, 10 to 14 hours each
   ([stage 0](stage-0.md)).
1. **Refactor locally:** run the whole pipeline on Postgres on the Mac, and
   prove it builds the same dataset as SQLite.
2. **Move files and backups to the cloud,** on today's production.
3. **Dry run on Dokploy:** the server runs everything for a week, but it
   cannot fetch or publish.
4. **Switch:** the server fetches and publishes, and the Mac retires. Releases
   pause for the import and its audit, expected in hours rather than the
   one to three days a re-derivation would have taken.

Before stage 1, a quick read-only check of the server confirms it has room.

After the switch, decisions move into Postgres one kind at a time, and the
published catalog fills. Read [the stages](stages.md) for steps, gates and
rollback, [stage 0](stage-0.md) for the invalidation change, and [the target
design](target.md) for the end state.

## Decisions the owner makes

| Decision                                             | Needed before           | Recommendation                                                                                                                                                 |
| ---------------------------------------------------- | ----------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Postgres major version                               | stage 1, step 3         | the newest one Dokploy supports, the same locally and on the server                                                                                            |
| Object storage provider                              | stage 2                 | either works with the plan, which always adds a copy at a second provider; R2 has no versioning, B2 keeps versions by default (not yet verified for this plan) |
| Postgres image                                       | stage 3                 | a custom image with continuous backup and point-in-time recovery                                                                                               |
| Publishing allowlisted decision tables with releases | after the switch        | yes, without author or note columns; never suppressions or identity decisions                                                                                  |
| Building the API                                     | after the catalog fills | separate work                                                                                                                                                  |

The plan picks a custom Postgres image and an allowlist as its defaults.
Either can change if the owner decides otherwise.

## Words used in this plan

| Word                          | Meaning                                                                                                                              |
| ----------------------------- | ------------------------------------------------------------------------------------------------------------------------------------ |
| Dokploy                       | A tool that runs Docker apps on your own server, with a web page to manage them                                                      |
| Docker, Docker Compose        | Docker packages a program with everything it needs; Compose starts several of these together                                         |
| Gate                          | A written check that must pass before the next stage starts                                                                          |
| Hold                          | A switch that stops the worker's loops from starting new work                                                                        |
| Out-of-band hold              | A hold kept outside the database, so it works while Postgres is down                                                                 |
| Tick                          | One run of one loop                                                                                                                  |
| Drain                         | Run until the work queue is empty                                                                                                    |
| Checkpoint                    | A saved copy of production's data                                                                                                    |
| Re-derivation                 | Re-running the derive loop over every unit when its inputs or code change; 10 to 14 hours on today's VM                              |
| Unit                          | One piece of derived work: one page's parse, one dancer's or event's projection, one event's links, one build                        |
| Transform                     | The code that turns a unit's inputs into its output: a page kind's extractor, parser or contract, a projector, the linker, the build |
| Declared version              | A hand-written version constant on a transform; after stage 0 it is what makes that transform's results out of date                  |
| Recipe, recipe scope          | A unit's recipe is the map of named inputs and digests its identity is hashed from; a scope is the code the recipe names             |
| Audit digest                  | The code or environment fingerprint recorded at each deploy for the record, but not part of any recipe                               |
| Differential proof            | Running new code over every stored input on a copy and comparing the output with what is stored, before a deploy                     |
| Current unit                  | A unit whose stored result is up to date with its inputs; only current units can be compared in a proof                              |
| Equivalence                   | A row saying that a new input fingerprint produced exactly an existing generation's output (migration 33)                            |
| Re-key                        | Recording equivalences so existing results count as current under new recipe values, without re-running them                         |
| Clone                         | A copy-on-write copy of a database file or a checkpoint (`cp --reflink` on btrfs or APFS) that costs nothing until written           |
| Cutoff                        | Keeping an existing generation when a re-run's output is identical, instead of writing a new one (D-0205)                            |
| Hash, digest, sha256          | A short fingerprint of some content; any change to the content changes it. sha256 is the method used                                 |
| Migration                     | A numbered script that changes the database's tables                                                                                 |
| Namespace                     | A group of tables in Postgres (Postgres calls it a "schema")                                                                         |
| Trigger                       | Database code that runs by itself when a row changes                                                                                 |
| Collation                     | The rule a database uses to sort text                                                                                                |
| Lock, advisory lock           | A claim that stops two jobs from writing at once; an advisory lock is one the program asks Postgres for by name                      |
| Connection pooler             | A middleman that shares database connections between programs                                                                        |
| WAL archiving                 | Saving Postgres's log of every change, so the database can be rebuilt                                                                |
| Base backup                   | The full copy that replaying the change log starts from                                                                              |
| Point-in-time recovery        | Restoring the database to any chosen moment by replaying that log                                                                    |
| Recovery point, recovery time | How much recent data a failure may lose, and how long until things run again                                                         |
| Object storage, bucket        | A service that stores files by name, such as S3, R2 or B2; a bucket is one storage area                                              |
| MinIO                         | Object storage you can run yourself; used here as a local stand-in                                                                   |
| psycopg                       | The Python library that talks to Postgres                                                                                            |
| Parquet                       | The column-based file format the dataset is published in                                                                             |
| Catalog                       | The published releases, stored in Postgres so they are easy to query                                                                 |
| `gov`                         | The tables that will hold decisions: overrides, approvals, holds, settings                                                           |
| Supersede                     | Replace a decision by adding a newer row, never by editing the old one                                                               |
| Redirect                      | A record that an old id now lives under a new one                                                                                    |
| Dead-man's switch             | An outside service that raises an alarm when regular pings stop                                                                      |
| Conditional request           | Asking a site to send a page only if it changed since a given copy                                                                   |
| User-Agent                    | The name a program gives a website when it asks for a page                                                                           |
| Egress check                  | A small test of how other sites treat requests from the server's address                                                             |

## Detail pages from the 2026-09-14 specification

These older pages hold detailed contracts. The stages link the parts they
reuse. Where they keep overrides as files, plan a one-container worker, fix
the Postgres version, or size the worker differently, the newer pages win.

| Topic                                  | Page                                      |
| -------------------------------------- | ----------------------------------------- |
| Scope and required access              | [Contracts and run inputs](contracts.md)  |
| Frozen inputs                          | [Inventory](inventory.md)                 |
| Connection, locks, schema and triggers | [Database port](database.md)              |
| Loading and checking a checkpoint      | [Import and independent audit](import.md) |
| Proving recovery works                 | [Checkpoints and restore](checkpoints.md) |
| Packaging, scheduling and monitoring   | [Worker and monitoring](worker.md)        |
| Whole-system checks                    | [Acceptance](acceptance.md)               |
| Provisioning, rehearsal and transfer   | [Cutover and recovery](cutover.md)        |

## Evidence

- [What the move must carry, 2026-10-19](../../../journal/investigations/2026/platform-survey-2026-10-19.md)
- [Hosting investigation, 2026-09-14](../../../journal/investigations/2026/dokploy-migration-2026-09-14.md)
- [PostgreSQL experiments, 2026-09-14](../../../journal/investigations/2026/postgres-migration-plan-2026-09-14.md)
- [Object storage investigation, 2026-09-14](../../../journal/investigations/2026/dokploy-object-storage-2026-09-14.md)
