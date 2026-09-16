# Port and test the database interface

Part of the [PostgreSQL migration plan](README.md). These are target
requirements, not evidence that migration has happened. Keep the numbered
steps in order and preserve the recorded operating holds.

## 5. M1 — PostgreSQL schema and database interface

### 5.1 Connection and row contract

Retain `open_database(state_dir, ...)` and the useful `Database` call surface,
adding a required database configuration supplied explicitly or from secret
files. A missing DSN is an error; never silently create SQLite state.
Reject `.sqlite`/`.db` runtime paths with a message pointing to legacy import.
`state_dir` continues to locate artifacts. Tests inject their own configuration.

Expose `transaction(immediate=True)` as a bounded write and
`transaction(immediate=False)` as a Repeatable Read, Read Only snapshot;
offer `read_snapshot()` as the clearer equivalent for converted callers.
Nested writes use savepoints and inherit the outer deadline. A read snapshot
cannot upgrade to writing; nested reads inside a write use that write's view
without pretending it is an independent snapshot. Invalidate snapshot caches
on exit, any write, and rollback to a savepoint. Normal autocommit reads must
not reuse snapshot caches.

Use Psycopg autocommit connections outside these explicit transactions, and
dedicated physical connections for session locks. Psycopg otherwise starts
transactions for ordinary reads; nested transaction contexts use savepoints.
[Psycopg transaction documentation](https://www.psycopg.org/psycopg3/docs/basic/transactions.html).

The row type supports `row[int]`, `row[str]`, `len(row)`, value iteration,
`keys()`, and `dict(row)` with SQLite-compatible behavior. Reject duplicate
column labels at the interface during tests and fix ambiguous SELECTs. Cursor
supports `fetchone`, `fetchmany`, iteration, `rowcount`, and explicit close.
Large scans use a named cursor within a read snapshot or bounded keyset reads.
No `fetchall` for an unbounded relation. Ordinary result iteration alone does
not establish a memory bound. The connection wrapper checks write context;
callers cannot invoke raw commit/rollback or obtain the underlying connection.

Map integrity SQLSTATEs to a stable `StateIntegrityError`; cancellation to
`WriteDeadlineExceeded` only after rollback is confirmed; lock acquisition
failure to existing command/control timeout errors; connection/commit ambiguity
to `StateConnectionLost`. Preserve trigger-specific invariant codes in errors.
Do not catch all database errors as “no result.” Never transparently reconnect
and resume a command that owned state or attempted an external effect.

### 5.2 Locks and deadlines

Reserve the two-int advisory lock namespace `0x5357494E` with keys `1` for
data-command ownership (A), `2` for control/admission turn (B), and `3` for
atomic writes (C). PostgreSQL scopes these locks to a database. Use the exact
namespace/keys in all worker, maintenance, and control paths. Session locks
span commits; transaction locks end with the transaction.
[PostgreSQL locking documentation](https://www.postgresql.org/docs/18/explicit-locking.html).

Acquire A with `pg_try_advisory_lock(namespace, 1)` on the command's primary
connection. Also keep an exclusive `flock` on the artifact volume's `state.lock`
for that command's lifetime. Order is artifact lock, A, B when needed, C.
Never acquire B while holding C. Acquire C with
`pg_advisory_xact_lock(namespace, 3)` as the first statement after BEGIN in
each application write transaction; subsequent statements get a current
Read Committed view. B is a session lock on the connection doing admission or
controls. Do not reenter/recount session locks accidentally; release once.

All data commands, backup, GC, and maintenance take artifact lock plus A.
Controls take B then C without A or artifact lock. New admissions take B then
C. Settling an admitted action takes C only. A waiting pause owns B while
waiting for an existing write; that writer can settle, and the next admission
cannot overtake the pause. Controls may commit while a backup snapshot is open.

Defaults: timer cycle waits zero for command ownership and logs a successful
overlap skip; ordinary commands wait at most 60 seconds; backup waits until
ownership or shutdown. Lock polling uses monotonic time, at most 100 ms sleeps,
and one shared caller deadline. Control servicing, including both locks and
commit, uses its remaining deadline. Read-only reports take neither A nor C;
filesystem report-cursor updates take a separate short report-file lock.

Preserve the 45-second whole-write limit. Start its monotonic deadline before
BEGIN, inherit it through savepoints, and include commit. Before SQL, set local
statement/lock timeouts to the remaining budget. Retain the main-thread Python
alarm for Python work; add a watchdog that cancels a blocked libpq operation at
the deadline. At 45 seconds cancel and roll back; if cancellation/rollback
cannot be confirmed within 5 more seconds, exit the process and classify the
outcome as uncertain. A supervisor keeps the artifact lock until the child is
dead. Do not configure a shorter server `transaction_timeout` that silently
releases ownership while Python continues. Test server-terminated sessions as
a distinct failure. Maintenance/import transactions have a separate explicit
deadline and never run under worker credentials.

Before every external source request or publication step, verify the command
connection is healthy and the action remains admitted. Connection loss stops
new actions; an already-issued remote request can have an uncertain outcome.
The volume lock prevents a replacement local worker until the old process is
dead. An advisory lock alone cannot fence an HTTP effect. Keep existing
publication reconciliation and durable uncertain admission records.

### 5.3 DDL, trigger, and role contract

Use UTF-8 with `LC_COLLATE=C` and `LC_CTYPE=C` for the database. Keep SQLite
integer contracts as `bigint`, floats as `double precision`, and serialized
JSON/timestamps/keys as text unless the explicit manifest-byte mapping below
applies. Preserve defaults and nullability. Add 0/1 checks only where the
existing contract requires them and source preflight proves compliance.
Review unique-NULL and nullable-primary-key behavior instead of letting a type
conversion silently strengthen or weaken the domain rule.

Add an `insertion_order bigint` identity to `derivation_generations`, imported
from its source rowid, and an `attempt_cursor bigint` identity to
`requirement_attempts`, also imported from rowid. Update every tie-break/report
query accordingly. Preserve all existing numeric IDs and sequences, including
`registry_verifications.verification_id`, `work_attempts.attempt_id`,
`control_events.event_id`, `requirement_transitions.transition_id`, and
`admission_decisions.decision_id`.

Create infrastructure tables in a separate `swingset_internal` schema:
`schema_migrations(version, sha256, applied_at)`, one-row
`state_identity(dataset_id, restore_generation, semantic_schema, import_run_id)`,
and import phase/table receipts. Normal application roles can read identity,
but cannot change it. Keep domain tables in `public`; revoke PUBLIC CREATE.
Migration checksums and semantic schema must agree at every ordinary open.

Maintain an explicit trigger mapping from every legacy trigger to a PostgreSQL
function/trigger and an invariant test. Consolidation is allowed; losing
coverage is not. Preserve derivation revisions, immutable generations and
identity journals, deleted-link retention, requirement transitions, queue
invalidation, and publication fencing. Determine deterministic ordering where
multiple triggers affect one mutation. Test inserts, updates, deletes, upserts,
multi-row statements, cascades, rejected writes, and rolled-back savepoints.
Canonical compound keys must match the existing SQLite JSON encoder for their
actual types. Implement explicit compact string/null array encoding with golden
Unicode, quote, slash, control-character, and empty-string cases; do not strip
whitespace from arbitrary serialized JSON. Fail the port ledger on an unmapped
numeric JSON key rather than applying a string encoder to it.

Create a NOLOGIN schema owner and separate login roles: migration, worker,
control, and reader/backup. Worker has domain DML/sequence usage but no ownership,
DDL, TRUNCATE, trigger-disable, or role-switch privilege. Control has SELECT
needed for selectors/status and DML only on `operator_pauses`, `control_events`,
`control_state`, plus required sequence usage. Reader/backup has SELECT only.
Migration credentials never enter the normal worker container. Prefer invoker
trigger functions; any needed definer function has a fixed safe search path,
minimal privileges, and no PUBLIC execute. Inventory grants as test data.

Advisory locks are cooperative. The supported application interface requires C
for all writes; grants do not make arbitrary worker SQL obey that lock. Test
the supported paths and restrict credential distribution. Preserve publication
guard triggers on every domain table except the existing five-table control/
admission exception in `publication_fence.py`; infrastructure tables are owned
separately. No ordinary startup installs or refreshes triggers.

**M1 exit:** fresh schemas 14 and 15 install; migration rerun is a checksum-
checked no-op; altered migration checksum is rejected; privilege, transaction,
row, cache, three-lock, timeout, and connection-loss tests pass on real PG.

## 6. M2 — Port the pipeline and prove semantics

Port by module group in this order, running that group's existing tests on
PostgreSQL after each change:

1. `state/` core, controls/admission, work/attempts, findings, requirements,
   identity journal, derivations, and report cursors.
2. `fetch/`, `admission/`, and `schedule/`: budgets, cache validators, issued
   requests, outcomes, clocks, host gates, fair turns, and crash recovery.
3. `history/`, `parse/`, `project/`, and `link/`: evidence and scope ownership,
   identifiers, policy gates, retained generations, and invalidation.
4. `build/`, `publish/`, then CLI, backup/GC entry points, and runtime reports.
   Check `doctor` connectivity through the database interface, not existence of
   `state.sqlite`. Remove equivalent filesystem assumptions everywhere.

For every SQL statement, bind values with `%s`, compose identifiers safely,
give conflict clauses explicit intended behavior, use `RETURNING` for inserted
IDs, and translate null-safe comparisons. Fix SQLite permissive GROUP BY,
scalar min/max, truthy integer predicates, aggregate return types, division,
date arithmetic, LIKE/case sensitivity, JSON extraction, and transaction-abort
behavior where the ledger finds them. Do not assume only placeholders change.
Use explicit ordering for deterministic results, including NULL placement and
tie-breakers. Preserve time precision/UTC interpretation. Use per-statement
savepoints only when the original caller intentionally recovered from an
integrity failure and continued its transaction.

Keep JSON strings exact. For `derivation_dependency_sets.manifest_json`, store
exact UTF-8 in a `bytea` column with `STORAGE EXTERNAL`; update all access through
`closure_manifest`. Hash in 65,536-byte slices before yielding any member, then
decode in a second bounded pass within the same read snapshot. The incremental
decoder can retain one member; reject an individual member over 8 MiB with a
named error, after proving all retained source members fit. Do not materialize
a complete registry manifest. Audit `derivation_readiness.py`, `derivations.py`,
and `build/closure.py` for alternate full-payload reads.

Retain a frozen legacy implementation for differential tests in an isolated
source snapshot made at M0, outside installed production packages. Run both
engines over identical deterministic fixture actions, fake clock values,
accepted policy, source responses, and generated IDs. Compare normalized
logical rows after each committed action and compare failure outcomes. Allow
only documented storage additions and genuine runtime identity differences;
do not mask timestamps, counters, findings, or public content wholesale.

Required scenarios include request budget reserve/issue/failure, retry-after,
conditional 304, pause/drain/resume, interrupted units, duplicate observations,
identity correction/deletion, trigger revision changes, same-timestamp release
selection, partial build, publication reservation, remote success with lost
acknowledgment, and recovery from pending candidate/baseline changes. External
effects use local fakes; no fixture test contacts a source or production Hub.

**M2 exit:** no runtime SQLite imports outside explicit legacy modules; every
call-site ledger row is closed; existing behavior tests pass with PostgreSQL;
schema-14 origin paths stay inactive; schema-15 origin tests still pass.
No test may be deleted or weakened merely because the engine changed.
