# 3. How the code talks to Postgres

Read [2. Schema](2-schema.md) first; next is [4. Hosting](4-hosting.md). This page is the contract for the
database code: where SQL lives, which values must come back unchanged,
how triggers, locks and timeouts work, and which roles exist. Stage 1
builds it; every rule here has a test.

## Where SQL lives

The port is Postgres-only. It lives on its own bookmark, rebased on `main`,
and merges at the switch; production keeps running today's SQLite code
until then. After the switch SQLite remains only as the importer's reader
for checkpoints, so there are no dual engines, no twin migrations and no
dialect layer to maintain.

- **A small engine core** (`state/engine/`): connections, transactions,
  locks, value decoding. No domain query lives there.
- **SQL stays in its caller,** rewritten for Postgres with `%s`
  placeholders, keeping the rules below so every value and hash comes back
  as SQLite gave it.
- **Backup and restore** move to Postgres's own tools
  ([4. Hosting](4-hosting.md#backups-and-recovery)).

## Values that must come back unchanged

Ids and checks hash stored text and whole rows, so the Postgres engine must
hand the code exactly the Python values SQLite does (`str`, `int`,
`float`, `None`). Golden hashes from real production rows pin this
([6. Stage 1](6-stage-1.md)).

| Trap                           | SQLite                                                                      | Postgres rule                                                                                                |
| ------------------------------ | --------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------ |
| a read outside `transaction()` | opens no transaction (`isolation_level=None`)                               | autocommit outside explicit transactions, or a stray read holds a snapshot                                   |
| row objects                    | `sqlite3.Row`: by index or case-insensitive name, `keys()`, iterates values | a row type that behaves the same; duplicate column labels rejected                                           |
| big result sets                | rows arrive one at a time                                                   | server-side cursors or bounded pages; never `fetchall` an unbounded table                                    |
| comparisons and `EXISTS`       | return `0` or `1`                                                           | cast to integer, or JSON writes `true`                                                                       |
| `sum()` over whole numbers     | returns `int`                                                               | cast to `bigint`, or Python gets `Decimal`                                                                   |
| two-argument `max(a, b)`       | NULL if either side is NULL                                                 | not `greatest()`, which skips NULLs; write the NULL case out                                                 |
| `IS ?`, `IS NOT ?`             | null-safe                                                                   | `IS [NOT] DISTINCT FROM`                                                                                     |
| `LIKE`                         | ignores ASCII case                                                          | `ILIKE` where case should not matter                                                                         |
| `GLOB`                         | exists                                                                      | rewrite each use                                                                                             |
| `julianday()` of bad text      | NULL                                                                        | a cast raises; guard it                                                                                      |
| bare columns in an aggregate   | allowed                                                                     | rewrite each use                                                                                             |
| `INSERT OR IGNORE`             | also hides NOT NULL and CHECK failures                                      | `ON CONFLICT DO NOTHING` covers unique conflicts only; state the intent per use                              |
| `INSERT OR REPLACE`            | deletes then inserts; cascades run                                          | `DO UPDATE` changes in place and fires UPDATE triggers; state the intent per use                             |
| an error inside a transaction  | later statements still run                                                  | every later statement fails; remove old-schema probes or use a savepoint                                     |
| row order without `ORDER BY`   | often rowid or index order                                                  | never assumed; add `ORDER BY` with a unique tie-breaker wherever order matters                               |
| sorting text and NULLs         | byte order; NULLs first                                                     | database created with `C` collation and `LC_CTYPE=C`; say `NULLS FIRST` or `NULLS LAST`                      |
| trigger timestamps             | `strftime('%Y-%m-%dT%H:%M:%f+00:00','now')`                                 | the same text, milliseconds and `+00:00`                                                                     |
| errors recognised by message   | "active publication fences", "locked", "busy"                               | the fence raises the same text or a fixed SQLSTATE mapped to the same exception; busy and locked by SQLSTATE |

Other rules:

- **Column order, defaults and nullability stay exactly as they are.**
  Triggers insert by position, and whole-row hashes depend on order. Do not
  tighten or loosen a constraint during the move.
- **Database errors are never "no rows".** Integrity, cancel, lock-timeout
  and lost-connection errors map to stable exception types.
- **Large manifests** (up to 259 MB) keep today's two passes in one
  snapshot: hash every 64 KiB slice, then decode members. Never hold a
  whole manifest in memory. If stored as `bytea`, use `STORAGE EXTERNAL`
  so a slice read fetches only the slice. Stage 1 measures `text` against
  `bytea` and decides. Test: a 256 MiB manifest adds under 128 MiB of
  memory. Every `blobopen` caller moves (`build/closure_manifest.py`,
  `build/closure_validation.py`, `state/collector.py`).
- **No extra JSON columns** on big tables; use a view or an expression
  index where a query needs a JSON field.

## Read tracking

The recorder (`state/read_sets.py`) records which **tables** a project or
link unit reads, and checks them against each kind's declared dependencies
(`RULES`). SQLite reports reads through its authorizer; Postgres has none.

- **At run time on Postgres,** a unit's read set is the tables its kind
  declares in `RULES`. Undeclared reads are not observed while running.
- **In the test suite,** armed mode (`SWINGSET_READS_ARMED=1`) asks
  Postgres's own planner which tables each unit-body statement reads:
  `EXPLAIN (GENERIC_PLAN, FORMAT JSON)`, once per statement text. It fails
  any unit that reads a table its kind does not declare. The planner
  resolves names against the catalog, expands views and CTEs, and leaves
  trigger bodies out, as the authorizer does.
- **The trade:** today production logs an undeclared read and link widens
  its read set from it. On Postgres the suite must catch it instead,
  before the code ships. The whole-graph verify run after each deploy
  still finds any stale result in bulk.
- **Needs Postgres 16 or newer** for `GENERIC_PLAN`; Rhuidean runs 17. Whether every statement
  explains this way is unverified; stage 1 runs the whole suite through it.

## Triggers

There are 809 today: 451 generated publication-fence triggers, 189
history-timing bumps, 50 that keep derivation change tokens, about 60
immutability guards, and the rest.

- **Recreate them in PL/pgSQL, row by row,** from the same rule lists
  (`publication_fence.py`, `NARROW`). Merging is fine; losing coverage is
  not.
- **One invariant test per family:** derivation input versions and
  revisions, immutable generations, identity journals, deleted-link
  retention, queue enqueue and invalidation, history timing, gaps and
  pressure, and the fences. Each covers insert, update, delete, upsert,
  multi-row statements, cascades, rejected writes and rolled-back
  savepoints.
- **The fence exempts seven tables:** the five control tables
  (`operator_pauses`, `control_state`, `control_events`,
  `execution_admissions`, `execution_dependencies`) and `backup_sets` and
  `backup_objects`. Fences for new tables are rebuilt by the migration job,
  never at ordinary startup.
- **Upserts:** Postgres fires BEFORE INSERT triggers even when ON CONFLICT
  finds a row. The narrow fence's rule, "an insert that adds no new key
  changes nothing", must carry over.
- **Firing order** is alphabetical by name in Postgres. Where two triggers
  act on one change, name them so the order is deliberate.
- **Keys built by triggers** are compact JSON text (`["calendar","x"]`),
  exactly as Python writes it (`ensure_ascii=False`, no spaces). Postgres's
  `json_build_array` adds spaces, so build the text by hand. Golden tests
  cover Unicode, quotes, backslash, slash, control characters, the empty
  string and NULL; numbers stay numbers.
- **Change counters** such as `history_dispatch_fence.revision` stay rows
  updated in the transaction. A sequence is not safe: its next value is
  visible before commit.

## Row numbers and sequences

- `rowid` columns the code reads keep their exact numbers at import:
  `derivation_generations`, `derivation_equivalences`,
  `derivation_dependency_sets`, `snapshots`, `watches`,
  `source_event_inventory`, `source_event_enumerations`,
  `scheduler_event_requests`, `event_pressure_dirty`, and the cursors that
  store them (`event_timing_cursor`, `event_progress_cursor`,
  `event_blocker_cursor`, the legacy row in `cursors`).
- **AUTOINCREMENT tables** start their identity above both the largest id
  and the `sqlite_sequence` mark, since deleted ids are never reused.
- **The collector's marks and the event cursors** assume a new row's number
  is higher than every committed row's. That holds only because every
  writer takes lock C before it inserts. Gaps from rollbacks are fine.

## Locks

Today's file locks (D-0215, `state/rwlock.py`) keep loops apart from each
other and from exclusive work, without loops waiting on each other.
Postgres keeps the same shape with advisory locks on the writing
connection. No connection pooler sits in between.

| Lock            | Who takes it                                                                               | Kind                                                      |
| --------------- | ------------------------------------------------------------------------------------------ | --------------------------------------------------------- |
| per loop        | each loop, for its tick                                                                    | exclusive per loop name                                   |
| state           | loop ticks shared; state-changing commands, backup, restore, `gc`, `accept-year` exclusive | session; an intent lock first so exclusive is not starved |
| heavy work      | release exclusive, derive shared                                                           | session                                                   |
| B, control turn | controls and admissions                                                                    | session; not re-entrant                                   |
| C, write        | every write transaction, as its first statement                                            | transaction; released at commit                           |

- **Order:** state, then B, then C. Never take B inside a write
  transaction. Controls take B then C without the state lock. An admission
  takes B then C; settling an admitted action takes C only. A waiting
  control holds B while an already-admitted write finishes, so the next
  admission cannot overtake it. A checkpoint holds B across its closure and
  copy (D-0164). B polls against a monotonic deadline; a control that times
  out commits nothing.
- **Loops never wait:** every shared step is a try
  (`pg_try_advisory_lock_shared`). A busy loop skips its tick. So
  acquisition keeps fetching during an hour-long build: the build holds
  state shared and heavy exclusive, and takes C only for its short writes.
- **Verify workers** use their own scratch databases, so C never
  serializes them ([5. Stage 0](5-stage-0.md#the-verify-run)).

## Timeouts and lost connections

- **The write limit** is 60 seconds for the write itself, not the lock wait
  (D-0215). Postgres has no progress handler, so set `SET LOCAL
statement_timeout` and `lock_timeout` to the time left. Savepoints
  inherit the deadline. If a cancel and rollback cannot be confirmed
  shortly after, the process exits and records the outcome as uncertain.
- **A server-side kill drops that session's advisory locks** while Python
  keeps running: `idle_in_transaction_session_timeout`,
  `transaction_timeout`, or an admin kill. Set these per role, longer than
  the longest normal gap (the release build's reader sits idle between
  reads). A lost session means lost locks: the tick stops.
- **A lock cannot take back an HTTP request already sent.** Before each
  source request or publication step, check the connection and the
  admission. Never reconnect and carry on after holding locks or making an
  outside call. A lost commit acknowledgement is "uncertain"; publications
  settle as uncertain, never as done.
- **Opening never migrates.** An ordinary open refuses a schema version or
  migration checksum that does not match the code. The migration job
  settles stale admissions (publications stay uncertain), changes the
  schema, then rebuilds fences. Today an exclusive open migrates, so this
  changes.

## Roles

| Role                     | Can                                                                          | Cannot                                                        |
| ------------------------ | ---------------------------------------------------------------------------- | ------------------------------------------------------------- |
| owner                    | own the schema; run migrations as a separate job                             | log in                                                        |
| worker                   | read and write pipeline rows, use sequences                                  | change the schema, `TRUNCATE`, disable triggers, switch roles |
| control                  | write `operator_pauses`, `control_events`, `control_state`                   | touch evidence or anything else                               |
| one per decider          | write `gov` rows; its name is recorded on each row                           | approve its own proposal (except the owner, with a flag)      |
| reader (later the API's) | read `catalog` views and a few evidence views (sources, snapshots, findings) | read page bodies, notes or suppressed people                  |

- Migration and owner passwords never enter worker containers.
- Trigger functions run with the caller's rights. A function that runs
  with its owner's rights gets a fixed search path and no PUBLIC execute.
- Grants are test data, and tests prove them.
- **Privacy in logs:** statement and parameter logging stays off, including
  statement text in error logs, because rows hold private notes and
  suppressed people. Each service sets `application_name`. Receipts and
  logs never contain passwords, connection strings, response bodies or
  private row values.

## After a big rewrite

A bumped transform's re-run rewrites dependency lists and leaves old copies
for `VACUUM`. Long read snapshots, such as an hour-long build, delay that
cleanup. Watch dead rows after each large re-run, and plan a `pg_repack`
when they grow. The planned size is about 40 GB, as today.
