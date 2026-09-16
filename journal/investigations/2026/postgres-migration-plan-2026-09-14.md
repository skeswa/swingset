# PostgreSQL and Dokploy migration plan

> Historical record. Dates, status claims, and instructions below describe the
> original work. See [current status](../../../docs/status.md) before acting.
> [Project journal](../../README.md)

For implementation, follow the [migration execution plan](../../../docs/plans/postgres-migration/README.md).
It resolves this report's proposals into package order, interfaces, acceptance
criteria, and a transfer that preserves the existing operating holds. This
report remains the dated research basis.

Investigated 2026-09-14. This develops the PostgreSQL target from the
[initial hosting review](dokploy-migration-2026-09-14.md). It is an implementation
proposal supported by code inspection, schema inventory results, official
documentation, and isolated PostgreSQL experiments. It does not change the
application or activate either writer.

## Recommended path

Port Swingset to PostgreSQL with Psycopg 3, preserve its current data and
publication semantics, and then deploy one worker alongside a dedicated
Dokploy PostgreSQL instance. Keep evidence files and Parquet candidates on a
local named volume. Use a custom, validated SQLite checkpoint importer and
one controlled cutover. Avoid ongoing dual writes.

Use PostgreSQL 18 at its current tested minor release, pinned by image digest.
The local experiment ran 18.6. PostgreSQL lists the 18 branch as supported
through November 2030. The probe used ARM64; build and rehearse the worker on
the Xeon server's actual architecture before production activation.
[PostgreSQL version policy](https://www.postgresql.org/support/versioning/).

The work has three independent acceptance gates:

1. **Database correctness:** PostgreSQL preserves the pipeline's transactions,
   admission rules, generation validity, identities, and publication recovery.
2. **Transfer correctness:** every imported row, ordering token, and referenced
   artifact is accounted for against a frozen checkpoint.
3. **Operating correctness:** the Dokploy deployment preserves holds, schedules,
   shutdown, backups, and the one-writer rule across restarts.

Do not combine these into one production experiment. Implement and rehearse all
three before changing the active writer.

## Evidence and scope

The inventory was produced from temporary empty SQLite databases created with
this checkout's migrations, without opening production. Inspection covered DDL,
columns, indexes, foreign keys, and Python query call sites. The findings below
are retained; temporary scripts and generated output were removed after research.

| Inventory                                            | Schema 14 | Schema 15 |
| ---------------------------------------------------- | --------: | --------: |
| Tables                                               |        71 |        74 |
| Explicit indexes, excluding SQLite automatic indexes |        82 |        87 |
| Triggers                                             |       290 |       299 |
| Publication guard triggers                           |       198 |       207 |
| Derivation triggers                                  |        75 |        75 |
| Other triggers                                       |        17 |        17 |

The Python scan finds 878 `.execute`, ten `.executemany`, and two
`.executescript` call sites. These are static syntax counts, not executed
query counts. Seventy-seven runtime files mention `sqlite3`; 100 test files
reference persistence. The biggest query concentrations are `state/`,
`schedule/`, `history/`, `project/`, and `build/`. This is a pipeline-wide port,
although many edits share a small number of patterns.

The [latest operating handoff](2026-09-15-release-handoff.md) records a held schema-14
writer, completed H16 scratch replay, and pending subsequent release work.
The current source declares schema 15. The three added schema-15 tables are
`history_origin_intents`, `history_origin_operator_refs`, and
`history_origin_requests`. Inventorying schema 14 from this checkout does not
prove it is byte-identical to the deployed schema-14 package.

**Working assumption:** the first PostgreSQL release preserves the approved
schema-14 data semantics; origin-backfill schema 15 remains separate. Before
implementation, establish the actual source pin and fresh checkpoint. If the
accepted release has already advanced, regenerate the inventory and explicitly
change the import contract. Do not let whichever checkout is on the import
path make this decision through automatic SQLite migration.

## 1. Persistence implementation

### Driver and module interface

Use synchronous Psycopg 3. The existing pipeline is synchronous and already
groups work into bounded transactions. An async rewrite, ORM conversion, and
support for two production database engines would add unrelated work.

Keep the `Database` module as the main seam. Its interface should own connection
lifetime, command ownership, bounded write transactions, consistent read
snapshots, nested savepoints, and error classification. Schema migration should
be an explicit maintenance operation; ordinary workers should check the
expected version and refuse a mismatch.

Use dedicated connections, with autocommit outside explicitly managed
transactions. Psycopg otherwise starts a transaction even for a plain SELECT;
this matters for locks and long-lived idle commands. Its transaction context
manager supports nested savepoints. Avoid transaction-pooled connections for
session advisory locks. [Psycopg transactions](https://www.psycopg.org/psycopg3/docs/basic/transactions.html).

Retain domain queries with their existing owners while translating them to
PostgreSQL. Do not build a general SQLite-to-PostgreSQL SQL rewriter. A small row
adapter may preserve positional access, named access, and `dict(row)` during
the port; document that interface explicitly. Psycopg dictionary rows alone
would break code that iterates a SQLite row expecting values.

Two other useful seams already exist: the checkpoint module can own database
snapshot plus artifact closure, and the dependency-manifest reader can own
bounded byte access. These modules should hide the new implementation details
from callers.

### Schema and SQL mapping

Create a PostgreSQL baseline migration for the selected semantic schema, followed
by PostgreSQL-only migrations with recorded checksums. Keep the old SQLite
migrations and interpreter available to read legacy checkpoints. Distinguish
the PostgreSQL storage migration version from the source checkpoint's schema
number; matching integers do not establish compatibility.

| Existing behavior                        | Initial PostgreSQL treatment                                                                                                                                                                      |
| ---------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `?` value placeholders                   | Psycopg `%s`; identifiers use `psycopg.sql.Identifier`. Review dynamically assembled queries and literal percent characters.                                                                      |
| SQLite `INTEGER`                         | Use `bigint` where full SQLite integer range matters; preserve existing 0/1 flag contracts with checks initially. Do not cast every integer to boolean.                                           |
| SQLite `REAL`                            | Use `double precision`, not PostgreSQL `real`. The probe reproduced precision loss through `real`.                                                                                                |
| JSON strings, timestamps, canonical keys | Preserve original text initially when bytes or string representation enter hashes. Translate date predicates explicitly and use native types only where their conversion contract is established. |
| `INSERT OR IGNORE`                       | Use explicit conflict targets with `ON CONFLICT DO NOTHING`; inspect existing NOT NULL/CHECK behavior, since SQLite IGNORE is broader.                                                            |
| `INSERT OR REPLACE`                      | Review each call; use intended `ON CONFLICT DO UPDATE` behavior. SQLite replacement can delete then insert, affecting references and triggers.                                                    |
| Null-safe `IS`/`IS NOT` comparisons      | Use `IS [NOT] DISTINCT FROM` for value comparisons, retaining `IS NULL` for null checks.                                                                                                          |
| `julianday`, `strftime`, JSON functions  | Translate queries and trigger bodies, preserving cutoff precision and compact key encoding.                                                                                                       |
| Default ordering                         | Specify NULL placement and deterministic tie-breakers; use compatible collation for IDs, ordering keys, and comparisons.                                                                          |
| `lastrowid`                              | Use `INSERT ... RETURNING` with explicit identity columns.                                                                                                                                        |
| Catalog/PRAGMA queries                   | Replace with PostgreSQL metadata queries or the known migration manifest. Remove hot-path existence checks where the required schema is already guaranteed.                                       |
| Connection change counters               | Make memoization local to an explicit read snapshot. Do not emulate `total_changes`/`PRAGMA data_version` with a query on every cache access. Disable reuse across writes and savepoint rollback. |

Psycopg provides safe identifier composition separately from value binding.
[SQL composition](https://www.psycopg.org/psycopg3/docs/api/sql.html).

SQLite's default ascending NULL order differs from PostgreSQL's. This was
reproduced locally; every scheduler/release ordering expression needs review.
Text collation also affects deterministic comparisons. Use a reviewed UTF-8
database configuration and explicit `COLLATE "C"` where byte ordering is the
contract, rather than relying on an installation's locale.
[PostgreSQL sorting](https://www.postgresql.org/docs/18/queries-order.html),
[collation](https://www.postgresql.org/docs/17/collation.html).

**Preserve these ordering identities explicitly:**

- `derivation_generations`: add an insertion-order column populated from source
  `rowid`; release selection currently uses it to break timestamp ties.
- `requirement_attempts`: add a monotonic cursor populated from source `rowid`;
  summary/report cursors must continue to address the same history.
- `derivation_dependency_sets`: `rowid` is only used to open a blob in the
  streaming reader; it need not become a public or semantic identifier.
- Preserve existing numeric IDs in registry verifications, work attempts,
  control events, requirement transitions, and admission decisions. Reset their
  sequences above imported IDs and any retained SQLite `sqlite_sequence`
  high-water mark. A maximum-row calculation alone may forget deleted IDs.

### JSON and large manifests

Do not convert every JSON text column to `jsonb`. Swingset hashes canonical
serialization, and PostgreSQL `jsonb` serialization changes whitespace and key
ordering. The local probe also shows that `json_build_array('project','event')`
adds a space compared with SQLite `json_array`, changing a derivation input key.
Port the canonical encoding contract and test Unicode, escaping, nulls, and
numbers. A two-string ASCII experiment is not a complete encoder proof.
[PostgreSQL JSON types](https://www.postgresql.org/docs/18/datatype-json.html).

For `derivation_dependency_sets`, use exact UTF-8 bytes in a `bytea` payload,
read through the manifest module in bounded slices. PostgreSQL's
`STORAGE EXTERNAL` allows uncompressed out-of-line values with efficient
substring access. The probe read 200,000 bytes in slices of at most 65,536 bytes
and reproduced the complete SHA-256. This proves the byte-access mechanism,
not registry-scale performance. Benchmark the storage cost and query overhead.
[TOAST storage](https://www.postgresql.org/docs/current/storage-toast.html).

Route other manifest access in `derivation_readiness.py`, `derivations.py`, and
`build/closure.py` through the same module, with explicit decoding only where
the caller needs a bounded complete object. Preserve the current verify-before-
consume behavior. Do not replace the incremental reader with a driver fetch of
the entire field.

Likewise, iterating a normal Psycopg cursor is not a memory bound: it normally
buffers the result on the client. Use named server cursors or bounded keyset
reads for large scans/import verification; avoid keeping an unbounded list in
the caller after introducing a streaming cursor.
[Psycopg cursor behavior](https://www.psycopg.org/psycopg3/docs/advanced/cursors.html).

## 2. Transactions, controls, and publication

### Preserve the existing concurrency contract

The current implementation relies on three kinds of serialization: the whole-
command file lock, a control/admission file lock, and SQLite's single writer.
Moving only the two file locks to PostgreSQL leaves the third behavior missing.
The probe demonstrated that an unrelated control update can commit while a
worker transaction is still open. Existing control tests expect a waiting pause
to commit after the current atomic write and before the next admission.

Proposed initial PostgreSQL lock protocol:

| Lock                   | Lifetime                                               | Callers                                                      |
| ---------------------- | ------------------------------------------------------ | ------------------------------------------------------------ |
| Data-command ownership | Session; spans multiple transactions and external work | Cycle, backup, restore, manual data mutation, GC             |
| Control/admission turn | Session; short, possibly waiting for the active write  | Pause/resume, new admissions, migration/restore coordination |
| Atomic write mutex     | Transaction                                            | Every pipeline/control write transaction                     |

Order: data-command ownership when required, then control/admission turn when
required, then atomic write mutex. A worker settling its already-admitted unit
takes only the write mutex and never waits for the control/admission turn.
Thus a queued pause can own the turn while waiting for the current write to
finish, and the next admission cannot overtake it. Use stable documented lock
keys; test cross-process behavior and connection loss.

PostgreSQL supports both session and transaction advisory locks. The probe
confirmed that session ownership survives commit, while transaction ownership
releases at commit. These locks are cooperative: every supported mutation path
must enter the protocol, and normal roles must not bypass the intended
interface. This lock design is proposed, not yet integrated or acceptance-tested.
[Advisory locks](https://www.postgresql.org/docs/18/explicit-locking.html).

Use Read Committed for serialized writes, reading current state in a statement
after the write mutex is acquired. Use explicit Repeatable Read, Read Only
transactions for coherent build/report snapshots. Acquiring a lock inside an
already stale snapshot does not refresh it. The default Read Committed view
can change between queries, which the probe reproduced.
[Transaction isolation](https://www.postgresql.org/docs/18/transaction-iso.html).

Preserve the 45-second whole-write deadline and the operator's remaining lock
budget. PostgreSQL statement/lock timeouts bound server waits, but they do not
alone bound Python work between statements. The PostgreSQL 18 transaction
timeout can provide an additional limit, but it terminates the session and
therefore releases its ownership lock. Cancellation, rollback, and session-loss
handling need tests under the selected Psycopg/libpq build.
[Timeout semantics](https://www.postgresql.org/docs/18/runtime-config-client.html).

Never silently reconnect and continue an in-flight command after losing its
ownership session. Stop new work, retain uncertain external outcomes, and use
normal recovery on a fresh invocation. Retry a database-only atomic unit only
when rollback is known; never automatically repeat a source request or Hub
publication because a connection or commit acknowledgment was lost.

### Triggers and roles

Port trigger families as reviewed PostgreSQL functions and trigger definitions:

- Derivation change tokens, generation immutability, and monotonic revisions.
- Identity journal immutability and preservation of deleted identity links.
- Requirement transitions and work invalidation/enqueue bookkeeping.
- Publication guards that reject semantic writes during an active publication
  while allowing controls to change.

Keep trigger-maintained changes in the same transaction as the originating
write. Check multi-row operations, upserts, deletions/cascades, null transitions,
and failed savepoints. PostgreSQL trigger execution details must not accidentally
double-increment counters or erase retained evidence.

Replace `set_authorizer` on the control connection with restricted PostgreSQL
privileges: a non-owner control role can read required state and write the
control tables it owns, but cannot migrate or alter evidence. Use separate
worker, reporting/backup, and migration credentials as needed. Test forbidden
writes directly, including trigger side effects. Provision ownership and grants
explicitly on restore; do not reuse Dokploy's control-plane database.

## 3. Transfer the database and artifacts

### Choose an explicit importer

Use Python's SQLite reader and Psycopg `COPY FROM STDIN` against a reviewed
PostgreSQL schema. This lets the importer preserve source IDs, hidden ordering
tokens, exact payload bytes, and the checkpoint's manifest while streaming rows.
Psycopg's row-copy interface handles text/null adaptation without hand-building
CSV. [Psycopg COPY](https://www.psycopg.org/psycopg3/docs/basic/copy.html).

`pgloader` can copy SQLite data, but its documented defaults include creating
and dropping tables, rebuilding indexes, and resetting sequences; its default
`real` mapping is PostgreSQL `real`. These defaults do not encode Swingset's
hash, precision, trigger, or row-order requirements. It is useful for an
exploratory copy, but the application needs an explicit importer either way.
[pgloader SQLite behavior](https://pgloader.readthedocs.io/en/latest/ref/sqlite.html).

### Import protocol

1. Select a remotely verified, immutable checkpoint. Record its archive commit,
   manifest hash, source/runtime pin, schema, accepted input bundle, public
   baseline, and pending publication intent. Read its SQLite file directly in
   immutable read-only mode; never invoke an automatically migrating opener.
2. Preflight storage and data: inspect SQLite integrity and foreign keys;
   inventory storage classes, NULL primary keys, invalid JSON, NUL/invalid text,
   non-finite floats, timestamp formats, sequence marks, and maximum payload
   sizes. SQLite's flexible typing must not become silent coercion or dropped
   rows. Report incompatible values for a deliberate decision.
3. Create an empty PostgreSQL target with no worker access and an inactive
   deployment marker. Create the approved tables, primary keys, and necessary
   constraints. Resolve cyclic foreign keys explicitly: scopes reference
   generations, and generations reference scopes. Attach/validate foreign keys
   after data loading where necessary, preserving the intended deferrable rules.
4. Stream each table with an explicit ordered column mapping. Populate semantic
   insertion-order columns from original `rowid`. Import large manifest bytes
   through a bounded path: ordinary `fetchmany` and `COPY.write_row` still
   materialize a single large field. Read those source fields with `blobopen`
   and stream escaped COPY blocks, or use a separately benchmarked chunk format.
   Journal completed tables and source hashes; a failed
   import remains inactive and can be rebuilt cleanly in another empty target.
5. Import history and generation tables without running their bookkeeping
   triggers. Otherwise loading findings could manufacture transitions and
   loading canonical rows could advance derivation versions. Install and verify
   those trigger functions afterward, under the maintenance role. PostgreSQL
   COPY normally fires triggers and checks constraints, so this must be part of
   the staging design. [COPY behavior](https://www.postgresql.org/docs/18/sql-copy.html).
6. Restore sequence high-water marks, including the empty-table case. Build the
   remaining indexes, validate every foreign key and check, apply grants, and
   run ANALYZE. No `ON_ERROR ignore`, dropped rows, or broadly disabled triggers
   are acceptable in the activated target.
7. Copy the artifact closure with relative paths and the baseline link intact.
   Verify each file's size/hash. Include operator holds, captured inputs,
   retained generations, pending candidate markers, and report cursors. Exclude
   the old venv and caches. Retain the original SQLite checkpoint separately.
8. Compare logical row counts and streaming hashes per table using a documented
   type/serialization mapping and primary-key order; compare new ordering columns
   against source row IDs. Validate source budgets, cursor positions, identity
   decisions, controls, generation selection, and baseline/pending intent
   individually. Preserve historical unfinished runs rather than relabeling them
   successful. Recovery can classify them through its normal rules later.
9. Bind this PostgreSQL state to this artifact volume using a persistent dataset
   identity and restore generation. The worker must reject a mismatched database
   URL/volume pair even if both separately look valid. Leave activation pending
   until restore verification and public-head reconciliation succeed.

The importer must support only explicitly reviewed source schemas. Supporting
all fifteen historical schemas immediately is unnecessary; old checkpoints can
be upgraded on an isolated copy with the matching legacy runtime when required.

## 4. Checkpoint and restore after the port

Preserve the complete-checkpoint contract and introduce a versioned PostgreSQL
checkpoint format. It should record database engine/schema, dump path and hash,
artifact manifest, dataset identity, accepted input hash, runtime/schema identity,
baseline/pending candidate, and tool versions. Readers must distinguish it from
the current SQLite format. Include legacy import and artifact-recovery tooling
in this change; current local checkpoint recovery and GC read SQLite directly.

Recommended normal backup protocol:

1. Acquire the data-command ownership lock, which also excludes GC, migrations,
   and baseline/candidate mutation through supported commands.
2. Begin a Repeatable Read, Read Only transaction. Export its snapshot with
   `pg_export_snapshot()` and use that same transaction to enumerate every
   database-referenced artifact. Keep the snapshot alive while the dump imports it.
3. Run matching-major `pg_dump --format=custom --snapshot=<snapshot-id>` into a
   temporary checkpoint directory. Use `--no-owner --no-acl` only with explicit
   role/grant provisioning during restore. Do not pass credentials in logged
   command strings. Operator control transactions may continue; both the dump
   and artifact enumeration see the same earlier snapshot.
4. Copy and hash the required immutable files and publication markers while
   the data-command lock still protects their lifetime. Atomically finalize the
   local checkpoint, then upload through the existing private archive transport.
   Keep current lock/upload/retry behavior initially; optimize it separately.
5. Record success only after remote acknowledgment. A retry or stale backup must
   remain visible. Preserve artifact-closure validation on download and restore.

PostgreSQL explicitly supports giving `pg_dump` an exported snapshot. The local
probe changed a row after snapshot export, dumped/restored, and recovered the
snapshot's old value. This establishes the database mechanism, not the complete
application protocol. [Exported snapshots](https://www.postgresql.org/docs/18/functions-admin.html),
[pg_dump snapshot option](https://www.postgresql.org/docs/18/app-pgdump.html).

Measure compressed versus uncompressed custom dumps inside the existing packed
HF transport. Compression and dump metadata may affect deduplication; do not
promise the SQLite transport's storage behavior without measurement. A backup
of the 2.46 GiB SQLite file is not a reliable estimate of the new database's
dump, indexes, WAL, or retained history size.

Restore into a fresh PostgreSQL database and separate artifact root under an
external activation hold. A marker only inside the database being replaced is
insufficient. Install state, verify hashes/constraints/grants, reconcile the
actual public head, and then clear the hold. Database creation, filesystem
installation, and remote verification are not one transaction, so interrupted
restoration must be resumable or safely restartable at every step.

Dokploy's independent database backup is an additional recovery copy; it does
not by itself include the evidence files or publication markers. Keep a restore
drill for the complete Swingset checkpoint, not only a successful database dump.

## 5. Dokploy deployment

Deploy a dedicated PostgreSQL instance and one worker. Use Dokploy's internal
connection details and explicitly put the worker on a network that can resolve
and reach that instance; independently created Compose networks do not become
connected merely because the resources share a project label. Verify the
installed Dokploy version's generated deployment and network attachment.
Leave the database's external port unset.
[Dokploy connection settings](https://docs.dokploy.com/docs/core/databases/connection).

PostgreSQL 18's official image uses `PGDATA=/var/lib/postgresql/18/docker` and
the volume mount at `/var/lib/postgresql`. Verify Dokploy's actual mount before
starting a custom 18 image; an older `/var/lib/postgresql/data` assumption is
not the same layout. Initialize locale/encoding explicitly, pin the image,
and validate persistence by recreating the container against the named volume.
[Official image instructions](https://raw.githubusercontent.com/docker-library/docs/master/postgres/README.md).

Worker packaging should:

- Install frozen Python dependencies and compatible PostgreSQL client tools at
  image build time; use the same image in rehearsal and deployment.
- Include exact source, migrations, config, overrides, and runtime recipe inputs.
  Record PostgreSQL schema/function and relevant collation/encoding identity in
  provenance; define which changes invalidate derived output deliberately.
- Read database credentials and `HF_TOKEN` from runtime configuration. Keep
  passwords and connection strings out of recipe capture, logs, and checkpoints.
- Mount the persistent artifact volume and adequate disk-backed temporary space.
- Run as a fixed non-root UID, forward shutdown signals to active work, and start
  held with publication disabled until explicitly activated.
- Preserve the cycle, backup, and summary cadence and restart behavior from
  `nix/module.nix`. A tested scheduler supervisor can invoke existing commands;
  Dokploy's scheduled exec jobs alone do not establish the needed lifecycle
  semantics. [Dokploy schedules](https://docs.dokploy.com/docs/core/schedule-jobs).

With 60 GB total RAM, provisionally budget 12–16 GiB for the worker and 4–8 GiB
for PostgreSQL, subject to free capacity and other workloads. These are rehearsal
budgets, not measured requirements. Limit connections and query memory, then
measure replay/build/audit and checkpoint peaks. Profile row-by-row queries:
an embedded SQLite call becomes a client/server round trip. Batch independent
writes and use COPY for imports where measurements justify it. Psycopg
`executemany` uses pipeline mode internally; COPY and server cursors have
different execution constraints.
[Psycopg batching](https://www.psycopg.org/psycopg3/docs/advanced/pipeline.html).

Monitor last completed cycle and checkpoint, intentional holds, pending restore
or uncertain publication, lock waits, database connections, disk/WAL growth,
and memory. Keep liveness separate from readiness and progress; `doctor` alone
does not establish that scheduled work recently completed.

## 6. Implementation work packages

| Order | Work                                                                                           | Evidence required to advance                                                                                                                |
| ----- | ---------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------- |
| P0    | Freeze semantic schema/runtime, checkpoint, and import contract                                | Exact pins and a manifest inventory; discrepancy with deployed state resolved                                                               |
| P1    | PostgreSQL baseline DDL, trigger families, connection/transaction module, real-DB test fixture | Empty schema installs; invariants, grants, savepoints, three-lock protocol, snapshots, and interruption tests pass                          |
| P2    | Port stage queries, canonical encodings, ordering, cache and bounded manifest access           | Fetch-gate, work, derivation, identity, build, controls, and publication tests pass on PostgreSQL; pure parser tests remain offline         |
| P3    | Streaming SQLite importer and complete audit                                                   | Every table/file accounted for; explicit sequence/order checks; interrupted import stays inactive; no source requests or public writes      |
| P4    | PostgreSQL checkpoint, legacy readers/import, GC and restore activation                        | New-format backup/restore drill plus missing/corrupt artifact, pending-publication, control-change-during-backup, and connection-loss tests |
| P5    | Docker image, scheduler, Dokploy provisioning, CI integration                                  | Final-image tests; hold/restart/overlap/retry/shutdown tests; private connectivity and volume persistence verified                          |
| P6    | Full-size rehearsal and controlled cutover                                                     | Logical output audit, acceptable memory/time/disk, fresh checkpoint, one selected writer, first new checkpoint and restore verified         |

P3–P5 can be developed once their interfaces are established, but P6 requires
all prior acceptance. The first useful engineering increment is P1 plus a
representative vertical slice: acquire a request budget, record synthetic source
evidence, commit a work unit, pause it from a second process, and recover after
connection loss. This tests the highest-risk semantics before converting hundreds
of call sites.

Use actual PostgreSQL in CI for persistence behavior. Scope a fresh database
per test or test worker, with clean role/lock namespaces; real commits and
separate processes are necessary for recovery and concurrency tests. A
transaction rolled back around the whole test cannot represent those cases.
Keep pure parsers/models on their current lightweight fixtures. Retain SQLite
fixtures specifically for the importer and legacy checkpoint tests.

Compare imported storage before accepting new runtime inputs. Then compare
business output after replay using controlled clocks and identical accepted
policy. New backend/runtime identities and run IDs can legitimately differ;
source evidence, identifiers, decisions, provenance relationships, and public
table content must match or have explicitly reviewed explanations. Do not use
Parquet file hashes alone as the equivalence criterion.

## 7. Cutover and rollback

1. **Before the window:** full rehearsal passed; image/schema/client pins fixed;
   PostgreSQL and artifact volumes provisioned but held; source credentials and
   Dokploy connectivity tested without collection; estimated transfer/replay and
   backup space measured.
2. **Freeze the old writer:** keep the persistent operator hold, stop all six
   old services/timers and manual mutations, verify no active source request or
   publication remains, and record actual public head. The file lock on the old
   SQLite host cannot coordinate with a new PostgreSQL advisory lock.
3. **Make the final checkpoint:** use the selected old runtime and obtain remote
   acknowledgment. Pin that archive commit. Copy/import only this checkpoint;
   preserve request budgets and next-allowed times so relocation causes no burst.
4. **Import and verify under hold:** run the audited importer, verify artifact
   closure and dataset identity, and reconcile baseline/pending publication with
   the actual public head. No ordinary worker may open an incomplete target.
5. **Controlled activation:** run doctor and the reviewed offline work first.
   A dry cycle still fetches sources and writes local state; use it only once
   source acquisition is intentionally allowed and the old writer is stopped.
   Review the candidate before first publication under the selected release's
   acceptance rules. Enable the scheduled writer once those checks pass.
6. **Confirm operation:** observe a completed cycle, preserved source limits,
   successful complete remote checkpoint, summary, and container restart. Perform
   a fresh PostgreSQL checkpoint restore drill before retiring the VM. Keep the
   old state and runtime held until the recovery window ends.

Before any new acquisition/publication, stopping the target and resuming the
unchanged held SQLite source is the simplest rollback. Once the target has
collected, altered controls, or published, the old state is stale: resuming it
can repeat traffic, lose decisions, or conflict with the public head. Prefer
repairing the PostgreSQL deployment or restoring a compatible complete
PostgreSQL checkpoint. Any reverse migration needs its own audited import and
publication reconciliation; a one-click image rollback cannot provide it.

## 8. Experiments executed and remaining limits

The disposable PostgreSQL 18.6 container had no network, host port, production
mount, or production credentials, and was stopped/removed after the experiments.
Its data lived on temporary memory-backed storage. These probes used `psql`,
not a ported Swingset or Psycopg implementation.

| Experiment                                   | Observed result                                                                               |
| -------------------------------------------- | --------------------------------------------------------------------------------------------- |
| Concurrent commit inside Read Committed      | Second SELECT saw the new value                                                               |
| Same experiment in read-only Repeatable Read | Both SELECTs saw the same value                                                               |
| Session advisory lock across commit          | Other connection could not acquire until explicit release                                     |
| Unrelated worker/control writes              | Both could proceed without a shared write mutex                                               |
| Transaction advisory mutex                   | Competing acquisition failed until transaction committed                                      |
| JSON array key and JSONB serialization       | Default bytes differed from compact SQLite/canonical text                                     |
| Ascending NULL order                         | SQLite NULL first; PostgreSQL NULL last                                                       |
| Floating point                               | PostgreSQL `real` lost precision that `double precision` retained                             |
| COPY and sequence reset                      | Null, empty string, tab/newline text survived; next generated ID was 44 after importing 41–43 |
| Bounded BYTEA reads                          | 200,000 UTF-8 bytes streamed in four slices with matching SHA-256                             |
| Exported-snapshot dump and restore           | Restored value 2 even though the live row changed to 9 after snapshot export                  |

The tested ARM64 image was
`postgres@sha256:1c59e2c3c818eaa0f0628f695b36e7c9e362d6b219b36a54a32df645cbd7e1af`.
The experiment scripts, raw output, and downloaded image were removed during
cleanup. The observations above remain a dated research summary; implementation
must establish its own regression tests and full-size acceptance evidence.

Still unverified: sandile.dev's installed Dokploy/Docker version and mount/network
configuration; free resources and competing workloads; actual current writer
pin/schema; full PostgreSQL trigger parity; whole-process cancellation under
Psycopg; full-size import/replay/build performance; complete PostgreSQL checkpoint
recovery; and the new scheduler's operating behavior. No production data was
read or transferred in this investigation, and no downtime estimate is justified
until the full-size rehearsal is measured.
