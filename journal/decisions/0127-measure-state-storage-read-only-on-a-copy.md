# D-0127: Measure state storage read-only on a copy, with digests never row bodies

Status: Proposed  
Recorded: 2026-09-18  
Accepted: —  
Acceptance source: —  
Topic: State retention and recovery cost  
Supersedes: —  
Superseded by: —

## Decision

Step 1 of the [bounded state plan](../../docs/plans/bounded-state-and-archive.md)
gets one tool,
[`journal/tools/runtime/measure_state_storage.py`](../tools/runtime/measure_state_storage.py),
built to these rules.

- **Read-only, provably.** The tool opens the measured database
  `mode=ro&immutable=1`, the mode
  [D-0102](0102-read-sealed-checkpoints-without-sqlite-sidecars.md) already
  requires for sealed checkpoints, so SQLite creates no `-wal` or `-shm`
  sidecar. It compares the file's size, modification time, and inode before and
  after, and with `--hash-database` its SHA-256 as well.
- **The live directory is refused mechanically.** The tool refuses any path
  under `/var/lib/swingset`, as its sibling tools do, for the database it
  measures and for every path it writes
  ([D-0135](0135-fence-every-path-the-measurement-writes-and-exit-on-the-gates.md)). It also refuses a
  database with a nonempty `-wal` or any `-shm` file: both mean a connection is
  open, and an immutable reader would silently ignore pending pages. Clearing
  the log needs `PRAGMA wal_checkpoint(TRUNCATE)` or closing every connection,
  and the refusal says so, because a plain `PRAGMA wal_checkpoint` reuses the
  file instead of truncating it.
- **Digests, not rows.** Output rows are streamed once. The tool keeps a set of
  payload SHA-256 digests per stage and unit kind and nothing else, so a 5 GB
  database does not have to fit in memory.
- **Distinct counts are per scope, and the total is an upper bound.** One set
  per stage and unit kind answers the plan's question. A second whole-database
  set would double peak memory for a number the plan does not ask for, so the
  totals figure is the sum of the per-scope sets, labelled as an upper bound.
- **Accounting is checked, not asserted.** `dbstat` bytes plus free-list bytes
  must equal `page_count * page_size`, allowing exactly one reserved lock page
  in a file over 1 GiB. Any other difference is reported as unaccounted pages.
- **Both row shapes are read.** `derivation_rows` is a table before the payload
  split and a view over `derivation_row_refs` and `derivation_payloads` after
  it. The tool reads through that name either way and records which shape it
  found, so it works on a held schema 29 backup and on a current database.
- **Backup timing is opt-in, fenced, and sized against the tree.**
  `--time-backup` writes only under the scratch directory it is given, refuses
  a scratch directory inside the measured state or under the live root, and refuses to start unless
  free disk covers two whole copies of the state tree, which is what it writes.
  [D-0132](0132-time-a-held-checkpoint-by-restoring-it-first.md) records the
  order it runs in. A timing failure is recorded as a finding; the report and
  the receipt are still written, because measuring a multi-gigabyte copy is the
  expensive half.
- **One compact receipt, tied to its source.** `--receipt` writes inputs,
  headline numbers, the ten largest objects, the per-stage row table, the
  gates, findings, and limits. Inputs name
  the measured copy, the manifest hash of the held checkpoint it came from, the
  command line, and the `--code-revision` the operator passes, so the numbers
  can be traced after the scratch tree is gone. The database SHA-256 appears
  only with `--hash-database`, because hashing a 5 GB file is a deliberate
  cost, and that digest is read once and reused. The full per-table report goes
  to `--output` in scratch storage and is not retained.

## Why

The plan forbids guessing which tables are big, and forbids touching
production. A tool that could write to the thing it measures would make the
copy-only rule a matter of care rather than a matter of fact, so read-only is
enforced by the open mode, the refusal of production paths and open databases,
and a before-and-after comparison of the file.

The distinct-to-total ratio is the single number the whole of step 2 rests on.
Computing it needs every payload hashed, which is why the tool streams and
keeps digests: loading the rows would need roughly the database's own size in
memory.

Splitting bytes in use from file size matters because the plan's cap and every
backup measure the file, while `dbstat` measures the pages that hold data.
Reporting one without the other would make a reclaim step look unnecessary.

Reading `derivation_rows` through whichever shape exists keeps one tool useful
across the step 2 migration. The measurement target is a held schema 29 backup,
but the tool has to keep working afterwards to show what step 2 actually saved,
which is the plan's step 2 gate.

Unverified: that SQLite keeps its reserved lock page off the free list in a file
over 1 GiB. The accounting check allows exactly one such page and reports the
difference, so a wrong assumption fails the check instead of passing quietly.

## Alternatives

- Read the live state directory directly. Rejected: a live database has a
  write-ahead log, so a reader would have to write the shared-memory file, and
  the plan forbids using production. Care is not a guard, so the refusal is a
  path check plus a sidecar check, not a sentence in a runbook.
- Keep the free-disk guard at twice the database file. Rejected: verified
  against the plan's own section 1 figures, a 5.0 GB database has an 8.8 GB
  checkpoint, so a guard sized on the database admits a run that then fills the
  disk partway through the restore.
- Use an ordinary `mode=ro` connection. Rejected: it creates `-wal` and `-shm`
  files in the directory it reads. Verified locally on a state database.
- Load the rows and count distinct payloads with SQL. Rejected: SQLite has no
  `sha256`, and `COUNT(DISTINCT payload_json)` on a multi-gigabyte column
  spills to a temporary file roughly the size of the column.
- Keep one whole-database digest set as well. Rejected: it doubles peak memory
  to answer a question the plan does not ask.
- Fold the measurement into `swingset doctor`. Rejected: this is a one-off
  investigation on a disposable copy, not an operating diagnostic. Doctor gains
  bytes-in-use reporting in plan step 3, under its own decision.

## Consequences

Step 1 can run without any production operation, any deployment, or any
network. The tool is one file with tests and no new runtime dependency.

Peak memory is one digest set per stage and unit kind, about 90 bytes per
distinct payload. A database with ten million distinct payloads would need
roughly a gigabyte. Unverified until the measurement runs; if it fails there,
the fix is to spill digests to a temporary SQLite table rather than to load the
rows.

The whole-database distinct count is not produced, only an upper bound. Nothing
in the plan needs the exact figure.

`--time-backup` needs free disk for two whole copies of the state tree, not two
copies of the database file. On the worker's figures that is about 17.5 GB, not
10 GB, and the tool refuses below it.

## Links

- [Implementation plan](../../docs/plans/bounded-state-and-archive.md)
- [Plan direction](0119-bound-state-by-interning-and-one-closure.md)
- [Investigation](../investigations/2026/state-storage-measurement-2026-09-18.md)
- [Sealed checkpoint read mode](0102-read-sealed-checkpoints-without-sqlite-sidecars.md)
- [Timing order for a held checkpoint](0132-time-a-held-checkpoint-by-restoring-it-first.md)
- [Write fence and gate-driven exit status](0135-fence-every-path-the-measurement-writes-and-exit-on-the-gates.md)
- [Evidence rules](../evidence/README.md)
