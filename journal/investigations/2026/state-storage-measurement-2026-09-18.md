# Measuring where state storage goes

Date: 2026-09-18 UTC
Status: Tool implemented and tested; the measurement ran on 2026-09-18 (see
"Measurement run" below). Step 1's decision is
[D-0166](../../decisions/0166-hold-interning-back-until-rows-repeat-and-cut-indexes-first.md).
Decision: [D-0119](../../decisions/0119-bound-state-by-interning-and-one-closure.md)
(plan direction), [D-0127](../../decisions/0127-measure-state-storage-read-only-on-a-copy.md)
(tool design),
[D-0132](../../decisions/0132-time-a-held-checkpoint-by-restoring-it-first.md)
(backup timing order),
[D-0135](../../decisions/0135-fence-every-path-the-measurement-writes-and-exit-on-the-gates.md)
(write fence and gate-driven exit status)

## Purpose

The worker's database is about 5 GB and its verified checkpoint about 8.8 GB.
Nobody knows which tables hold those bytes. Step 1 of the
[bounded state plan](../../../docs/plans/bounded-state-and-archive.md) says to
find out on a throwaway copy before writing any migration, because the whole
case for step 2 rests on one number: how many output rows repeat between
generations. This note says what the tool measures, how an operator runs it,
and what still has to happen before step 1 can be called done.

## What the tool measures

[`journal/tools/runtime/measure_state_storage.py`](../../tools/runtime/measure_state_storage.py)
opens one state database and writes a report as canonical JSON:

- **Bytes per table and index**, from SQLite's `dbstat` virtual table: name,
  kind, pages, and bytes. It checks that those bytes plus the free list account
  for `page_count * page_size`, allowing the one page SQLite reserves in a file
  over 1 GiB and reporting any other difference.
- **File sizes**: `state.sqlite`, its `-wal` and `-shm` sidecars, the page size,
  and the free-list bytes. Bytes in use and file size are reported separately,
  because deleting rows frees pages without shrinking the file.
- **Per stage and unit kind**: generations, output rows, payload bytes, and how
  many distinct payload digests those rows have. The distinct-to-total ratio is
  what predicts step 2's saving. The tool streams the rows and keeps only a set
  of digests, never the row bodies.
- **Declared rows against rows that read back.** Each generation records how
  many rows it produced. The tool counts the rows that actually stream out and
  compares the two. A generation whose payload bytes are not local declares
  rows that no longer stream, which would otherwise shrink the ratio above
  without saying anything.
- **Identity and operating history**: row counts and `dbstat` bytes for every
  `identity_*` table and for `control_events`, `work_attempts`, `runs`,
  `findings`, and `finding_support`, so the plan can tell whether interning
  output rows is even the right target.
- **`--time-backup`** (optional): times a restore, a checkpoint, and that
  checkpoint's verification into scratch, and reports the bytes each used. A
  copied held checkpoint is restored first and the restored tree is then backed
  up; a plain state directory is backed up first and that checkpoint restored
  ([D-0132](../../decisions/0132-time-a-held-checkpoint-by-restoring-it-first.md)).
  The run writes two whole copies of the state tree, so it refuses to start
  unless free disk covers both.

The tool ends with a **gates** object: page accounting closed, declared rows
matched readable rows, every row named a generation, and the timing leg (if
asked for) succeeded. It exits non-zero when any of those failed, and the
findings name which. A run that exits 0 is a run whose counts matched.

The tool never writes to the database it measures, and refuses the live one. It
opens `mode=ro&immutable=1`, the mode
[D-0102](../../decisions/0102-read-sealed-checkpoints-without-sqlite-sidecars.md)
already requires for sealed checkpoints, so SQLite creates no sidecar files. It
refuses any path under `/var/lib/swingset`. It refuses a database with a
nonempty `-wal` or any `-shm` file, even an empty one, because both mean a
connection is open and an immutable reader would silently ignore pending pages.

Every path the tool writes to is fenced the same way and checked **before** the
measurement starts: `--output`, `--receipt`, and `--scratch` may not sit under
`/var/lib/swingset`, may not sit inside the copy being measured, and may not
already exist
([D-0135](../../decisions/0135-fence-every-path-the-measurement-writes-and-exit-on-the-gates.md)).
A file written inside a copied checkpoint stops that copy verifying against its
own manifest, and a destination refused after the run would throw away hours of
reading.

Tests are in [`tests/test_measure_state_storage.py`](../../../tests/test_measure_state_storage.py).
They build a state whose generations share payload bodies, check the per-stage
counts and the distinct-to-total ratio, check that every `dbstat` object is a
known table or index and that the page accounting closes both with and without
freed pages on the free list, check that the database bytes are unchanged after
a measurement, check that a generation with a missing payload fails the
declared-versus-readable gate, and check each refusal: the live root for the
measured database and for every destination, a destination inside the copy, a
destination that already exists, an open database, an empty shared-memory file,
a non-truncated write-ahead log, and too little free disk. `--time-backup` is
exercised twice, once on a plain state directory and once on a copy of a sealed
checkpoint, which is what an operator actually measures.

## What an operator runs

Run this on the worker, against a **copy of a held checkpoint**. Never point
the tool at `/var/lib/swingset` itself: a live state directory can have a
nonempty write-ahead log, and the point of step 1 is that nothing in production
is touched.

Unverified: whether this worker has a repository checkout with `nix develop`
available. If it does not, copy the checkpoint to a machine that does, or
install the tool's one dependency (Python 3.12 with SQLite 3.50, for `dbstat`)
and run it with the repository `src/` on `PYTHONPATH`.

```sh
# 1. Choose a held checkpoint and measure it, so the space sums below are real.
sudo ls -1 /var/lib/swingset/checkpoints
sudo du -sb /var/lib/swingset/checkpoints/<run id>
df -B1 /var/tmp
```

`--time-backup` writes two whole copies of that tree, on top of the copy made
in step 2. So `/var/tmp` needs about **three times** the checkpoint's size
free, and the tool refuses to start if it has less than two. On the plan's
section 1 figure of 8,770,615,161 bytes that is roughly 26 GB. If `/var/tmp`
shares a filesystem with `/var/lib/swingset` (unverified for this worker; check
with `df /var/tmp /var/lib/swingset`), use a scratch filesystem that does not,
or skip `--time-backup` and run it separately later.

```sh
# 2. Copy it to scratch. Copy; never move, rename, or delete the original.
WORK=/var/tmp/swingset-state-measure-20260918-001
sudo install -d -m 700 "$WORK"
sudo cp -a /var/lib/swingset/checkpoints/<run id> "$WORK/copy"
sudo chown -R "$(id -un)" "$WORK"

# 3. Check the copy against its own manifest before measuring it.
cd /path/to/swingset
nix develop -c uv run python -c "
from pathlib import Path
from swingset.backup.checkpoint import verify_checkpoint
from swingset.state.db import SCHEMA_VERSION
print(verify_checkpoint(Path('$WORK/copy'), maximum_schema_version=SCHEMA_VERSION)['schema_version'])
"

# 4. Measure it. The full report and the backup timing stay in scratch.
nix develop -c uv run python journal/tools/runtime/measure_state_storage.py \
  --state "$WORK/copy" \
  --output "$WORK/report.json" \
  --receipt "$WORK/receipt.json" \
  --hash-database \
  --code-revision "$(jj log -r @- --no-graph -T commit_id)" \
  --time-backup --scratch "$WORK/timing"
```

Step 4 prints the findings and exits non-zero if any gate failed. Read the
findings and `"$WORK/report.json"` before retaining anything.

Notes on step 4:

- `--output`, `--receipt`, and `--scratch` must all be new paths outside
  `"$WORK/copy"` and outside `/var/lib/swingset`. The tool checks them before it
  opens the database, so a wrong path costs a second, not a measurement. Note
  that `"$WORK"` and `"$WORK/copy"` differ by one path segment: writing the
  report into the copy would stop that copy verifying against its own manifest.
- The exit status is the gates: page accounting closed, declared rows matched
  readable rows, every row named a generation, and the timing leg succeeded.
  Non-zero means one of those failed and the findings say which. Do not treat a
  non-zero run as done.
- The tool refuses `/var/lib/swingset` outright, and refuses any database with
  a non-empty `-wal` or with a `-shm` file at all, even an empty one, so it
  cannot measure the live state by accident. If
  a copy is refused for a non-empty write-ahead log, run
  `PRAGMA wal_checkpoint(TRUNCATE)` on it or close every connection to it. A
  plain `PRAGMA wal_checkpoint` reuses the file instead of truncating it and
  does not clear the refusal.
- `--time-backup` restores the copy into `"$WORK/timing/restored"`, then backs
  that up into `"$WORK/timing/checkpoint"` and verifies it. The restore figure
  therefore includes verifying the held copy, and the backup figure covers a
  real state directory with its baseline symlink in place. The report's
  `backup_timing.order` says `restore-then-backup`.
- If the timing leg fails, the report and the receipt are still written and the
  tool exits non-zero. Retain the receipt anyway; the failure is in it. To rerun
  the timing, use fresh `--scratch`, `--output`, and `--receipt` paths: all
  three must not already exist, so bump the run number in `$WORK` or add a
  suffix to each file.
- `--hash-database` reads the whole file twice. Drop it on a 5 GB copy if the
  read is the thing holding you up; the receipt then records `null` for the
  hash, and the manifest hash of the source checkpoint still identifies it.

Then retain exactly one small receipt and delete the scratch tree:

```sh
mkdir -p journal/evidence/runtime/state-storage-2026-09-18
cp "$WORK/receipt.json" journal/evidence/runtime/state-storage-2026-09-18/
mise run evidence-size
rm -rf "$WORK"
```

Then link the new bundle from its topic guide, which the
[evidence rules](../../evidence/README.md) require: add a row to
`journal/evidence/runtime/README.md` naming
`state-storage-2026-09-18/receipt.json`, what it measured (the held checkpoint,
by run id), and this note. A bundle nobody can find from the topic guide
satisfies step 1 in form only.

The receipt is a few kilobytes: inputs (the measured copy, the source
checkpoint's manifest hash, the command, the code revision, and the database
hash), the largest objects, the derivation totals, the per-stage row table with
its distinct-to-total ratios, the gates, the backup timing, the findings, and
the limits. The per-stage table is retained because the scratch report is
deleted and those ratios are the argument for or against step 2. The
full per-table report is tens of kilobytes to low hundreds and belongs in
scratch, not in version control. Follow the
[evidence rules](../../evidence/README.md): one compact receipt per run, gzip
new generated text over 256 KiB, and nothing over 1 MiB in the repository.

## What "done" requires

The plan's step 1 is done when all three hold:

1. The counts match the copy, and every table and index is accounted for. The
   report's `gates` object is the check: `page_accounting`,
   `declared_rows_match`, and `rows_all_name_a_generation` must all be true, and
   the tool exits non-zero if they are not. The `findings` say so in words.
2. One small receipt is retained under `journal/evidence/runtime/`.
3. A decision record names which later steps run and in what order. If output
   rows are not the biggest cost, that record says what is and adds a step for
   it. That decision cannot be written from this note; it needs the numbers.
   [D-0158](../../decisions/0158-build-steps-2-and-3-before-the-measurement-and-gate-the-rest-on-it.md)
   records that steps 2 and 3 were built before this measurement and what still
   waits for it. It does not stand in for the decision this clause asks for.

## Status on 2026-09-18

The measurement ran on the worker the same day against the scratch restore of
held checkpoint 004 kept under `/var/tmp` for this purpose; the sections above
were written before that copy was found. Results and the step 1 decision are
in "Measurement run, 2026-09-18" below and in
[D-0166](../../decisions/0166-hold-interning-back-until-rows-repeat-and-cut-indexes-first.md).

## Measurement run, 2026-09-18

The measurement ran on the worker against
`/var/tmp/swingset-schema29-restore-20260917-004/restored-state`, a scratch
restore of held checkpoint 004 (schema 29) kept for this purpose, using the
service's Python and the working-copy tool at revision `ttnwxvylxqvq`. It read
the database immutably, left it unchanged, and took 20 seconds. Receipt:
[receipt-001](../../evidence/runtime/state-storage-measurement-2026-09-18/receipt-001.json);
full report:
[report-001](../../evidence/runtime/state-storage-measurement-2026-09-18/report-001.json).
The first run's backup timing failed on a missing `libstdc++` because the
service's `LD_LIBRARY_PATH` was not set. A second run with it set passed every
gate ([receipt-002](../../evidence/runtime/state-storage-measurement-2026-09-18/receipt-002.json),
[report-002](../../evidence/runtime/state-storage-measurement-2026-09-18/report-002.json)).

| Measure                  | Value                                              |
| ------------------------ | -------------------------------------------------- |
| File size                | 5,016,936,448 bytes                                |
| Bytes in use (dbstat)    | 5,016,932,352 bytes; free list 0                   |
| Generations              | 34,987                                             |
| Output rows              | 1,669,989, all readable, 502,109,081 payload bytes |
| Distinct payload digests | at most 1,669,607 (0.9998 of rows)                 |

Largest objects, table plus its indexes:

| Table                        | Bytes         | Share                                                   |
| ---------------------------- | ------------- | ------------------------------------------------------- |
| `derivation_rows`            | 1,366,700,032 | 27.2% (the table 874 MB, its two unique indexes 492 MB) |
| `source_generations`         | 731,418,624   | 14.6%                                                   |
| `canonical_scope_rows`       | 532,299,776   | 10.6%                                                   |
| `derivation_dependency_sets` | 438,067,200   | 8.7%                                                    |
| `identity_link_history`      | 308,011,008   | 6.1%                                                    |
| `callback_marks`             | 279,220,224   | 5.6%                                                    |
| `derivation_generations`     | 262,569,984   | 5.2%                                                    |
| `observations`               | 185,257,984   | 3.7%                                                    |

What the numbers say:

- Rows do not repeat on this copy. Every scope has one generation per unit
  (2,541 event scopes, 2,541 `project/event` generations, 2,541 `link/event`
  generations), so nothing has been recomputed yet and interning by digest
  would save about 0.02% of row bytes today. The plan's step 2 premise holds
  only after recomputation produces history; this copy has none.
- The derivation machinery (rows, dependency sets, generations) is 41% of the
  file, and `derivation_rows` alone is the largest object. Its unique indexes
  cost more than half of the table again.
- `source_generations` (15%) and `canonical_scope_rows` (11%) are the next
  costs. Neither is touched by the plan.
- Labels are not small at this scale: `derivation_generations` plus
  `derivation_dependency_sets` are 700,637,184 bytes for 34,987 generations,
  about 20 KB per generation and 14% of the file. The plan assumed a few
  hundred bytes per row; the dependency-set manifests are the bulk of it.

### Backup and restore timing

From the second run, backup-then-restore order, one run on the worker with an
uncontrolled cache: a checkpoint of the 8.77 GB state tree (68,866 files) took
268 seconds to create and 43 seconds to verify, and restoring it took 227
seconds. The run wrote 17.55 GB under scratch, so a timing run needs free disk
of about twice the state tree, as the tool requires.

### Step 2 measured on the same copy

To answer the plan's step 2 gate without guessing, a copy of the copy was
migrated from schema 29 to 32 with the working-copy code and then reclaimed in
place ([migration-30-measurement](../../evidence/runtime/state-storage-measurement-2026-09-18/migration-30-measurement.json),
[receipt-schema30](../../evidence/runtime/state-storage-measurement-2026-09-18/receipt-schema30.json)).

| Moment                          | File size     | Bytes in use  | Free list     |
| ------------------------------- | ------------- | ------------- | ------------- |
| Before                          | 5,016,936,448 | 5,016,932,352 | 0             |
| After migration, before reclaim | 6,890,405,888 | 5,523,730,432 | 1,366,671,360 |
| After reclaim (`VACUUM`)        | 5,333,405,696 | 5,333,401,600 | 0             |

The migration took 92 seconds and the reclaim 34 seconds. The interned layout
(`derivation_row_refs` 1,007 MB plus `derivation_payloads` 789 MB, indexes
included) is 430 MB larger than the inline table it replaced (1,367 MB), because
every reference now carries a 64-character digest in the table and in its
unique index while almost no payload is shared. On this copy step 2 makes the
file 6.3% larger, not smaller, and the file peaks at 6.9 GB during the
migration, so the migration needs about 1.9 GB of headroom.

## Conclusion

Interning cannot help until recomputation produces repeated rows, and this copy
has none: every scope has one generation. The measured costs are, in order,
the derivation rows and their unique indexes, source generation JSON,
canonical scope rows, dependency-set manifests, identity link history, and
callback marks. The plan's step 3 (which generations must stay local, and
removing the rest after archiving) addresses the count of generations, which is
what grows; step 2 addresses bytes per copy, which do not repeat yet. The
recommendation in the step 1 decision record is to hold schema 30 back from
production until a re-measurement after sustained cycles shows a distinct-to-
total ratio low enough to pay for the 430 MB of reference overhead, and to add
a step for the derivation row indexes and the source generation JSON, which
together are 24% of the file.

Later note: the migrations were reordered after this was written, so interning
is schema 32 and the numbers above that call it schema 30 mean the same
migration ([D-0167](../../decisions/0167-intern-derivation-payloads-in-the-last-migration.md)).

## Storage-driver follow-up, 2026-09-18

D-0166 also asked which derivation indexes and source-generation JSON columns
carry the next block of bytes before any redesign. Tool version 4 adds that
attribution without returning or retaining JSON bodies. Index page sizes still
come from `dbstat`; JSON figures are logical UTF-8 byte counts and cannot be
read as predicted page savings.

The tool ran as root on the worker against the same closed schema-29 scratch
restore, with no backup timing leg and no production or network operation. It
hashed the 5,016,936,448-byte database as
`5840f97da69c42adb6e60132418c64c04505ae043a650723c84ff38644c64265`,
found no SQLite sidecars, passed every gate and verified that the database did
not change. The tool revision recorded by the run is
`e03f14f0dbc65f47b7d9b9f95da6b2a97f6c2beb`; the tool file's SHA-256 is
`21a2513234ac4400851659bb502630e979ef24ff97bec14cb9ff9e1ca3a74024`.

Retained output:

- [report-003](../../evidence/runtime/state-storage-measurement-2026-09-18/report-003.json),
  SHA-256 `941384e9b7bcc36af0c0a6007484a2536fa75a3ea3880b54946c53bf65c4664f`;
- [receipt-003](../../evidence/runtime/state-storage-measurement-2026-09-18/receipt-003.json),
  SHA-256 `4d0a12f9f5c870aa3dcc518fc311c0175b0aeb3027eac542f20a34937a428e5b`.

The 34 focused measurement tests passed, including skewed UTF-8 columns,
implicit and expression indexes, the pre-interning table, the post-interning
view and absent tables. The final combined working copy passed all 3,034
offline tests in 573.86 seconds, Ruff, and mypy over 232 source files.

### Results

| Object or column                                                  | Bytes       | Meaning                 |
| ----------------------------------------------------------------- | ----------- | ----------------------- |
| `derivation_rows` uniqueness index: generation, table, record key | 342,245,376 | Physical `dbstat` bytes |
| `derivation_rows` primary-key index: generation, ordinal          | 150,556,672 | Physical `dbstat` bytes |
| `source_generations` table                                        | 720,551,936 | Physical `dbstat` bytes |
| All `source_generations` indexes                                  | 10,866,688  | Physical `dbstat` bytes |
| `report_json`                                                     | 386,379,672 | Logical UTF-8 bytes     |
| `result_json`                                                     | 173,890,464 | Logical UTF-8 bytes     |
| `recipe_json`                                                     | 109,815,446 | Logical UTF-8 bytes     |
| `manifest_json`                                                   | 16,804,780  | Logical UTF-8 bytes     |
| All four JSON columns                                             | 686,890,362 | Logical UTF-8 bytes     |

The source-generation table has 32,189 rows and no null JSON values.
`report_json` is broad rather than driven only by rare outliers: its median row
is 11,075 bytes and its 90th percentile is 17,713 bytes. `result_json` is more
skewed: its median is 1,035 bytes, its 99th percentile is 78,331 bytes and its
maximum is 1,539,302 bytes.

### Recommendation, not a decision

Do not remove a source-generation index to chase this cost: all six together
are only 11 MB. Start the next design investigation with the repeated structure
inside `report_json` and with the long text keys carried by the two 493 MB
derivation indexes. First inventory the queries and uniqueness rules those
indexes serve. Then measure one compact representation on a disposable copy,
including migration time, integrity, query plans, file size and backup size.
The retained JSON is evidence, so a design must preserve exact reconstruction
and must not infer savings from logical bytes alone. No schema change is chosen
by this investigation.

## Query, JSON and layout follow-up, 2026-09-18

The next investigation completed the plan's query inventory and measured one
concrete layout on two fresh full-database copies. It did not change the sealed
schema-29 source, production, or the network.

### What the derivation indexes protect

`PRIMARY KEY(generation_id, ordinal)` has four jobs. It rejects two rows at one
stream position, gives every generation a stable output order, supplies the
common generation lookup, and lets schema 32 stream the old table in exact
generation and ordinal order. The completion, closure and restore paths all
read `WHERE generation_id=? ORDER BY ordinal`; retention counts and groups by
the same prefix.

`UNIQUE(generation_id, table_name, record_key)` rejects two logical rows with
the same identity inside one generation. `retain_output` relies on both unique
constraints to prove that the stored rows are an exact image of the one-pass
fingerprinted stream. The index also serves artifact lookup by generation and
table. The completed-build receipt query additionally filters by record key and
JSON content. Dropping either constraint would change the accepted integrity
contract, so the prototype keeps both.

The rows are immutable. Their deferred foreign key permits the sole writer to
stream rows before it writes the generation label, while commit still rejects
an absent label. History-dispatch triggers advance the timing fence on row
changes. A rebuild must preserve the foreign key, both unique constraints, the
immutability triggers and the fence triggers together.

### Exact source-generation evidence

The four JSON documents cannot be recreated from current watches,
observations, accepted inputs or snapshots. Those tables can change and do not
cover rejected or superseded generations. The retained documents own the
ordered artifact manifest, exact parse recipe, ordered parse result and full
admission report. Existing readers use all four, raw report bytes participate
in review digests, and `recipe_json` has direct SQL `json_extract` readers plus
the retained-source expression index. A compact representation therefore has
to reconstruct the original UTF-8 bytes and preserve the extracted recipe
fields used by SQL.

The bounded read-only profiler found that every current document is canonical
JSON and left the source signature unchanged. Whole-document sharing is too
small to justify a four-column dictionary:

| Column          |   Logical bytes | Distinct documents | Per-row zlib-6 bytes |
| --------------- | --------------: | -----------------: | -------------------: |
| `manifest_json` |      16,804,780 |             31,805 |            9,935,581 |
| `recipe_json`   |     109,815,446 |             32,189 |           34,421,385 |
| `result_json`   |     173,890,464 |             31,754 |           23,392,043 |
| `report_json`   |     386,379,672 |             31,805 |           48,790,097 |
| **Total**       | **686,890,362** |                    |      **116,539,106** |

The 83% reduction is an upper bound on possible storage savings before SQLite
framing and any columns needed for SQL. Structural sharing is concentrated more narrowly:
`recipe_json.accepted_inputs` contains 1,545,072 items but only 51 distinct
values; `report_json.fields` contains 2,084,911 items and 20,897 distinct
values; `report_json.guards` contains 817,991 items and only 33 distinct values.
These are candidates for a later exact reconstruction prototype. Whole result
observations share much less. Compression or structural interning is not a
schema choice from this measurement alone.

Retained output:

- [source JSON report](../../evidence/runtime/state-storage-measurement-2026-09-18/source-json-report-005.json),
  SHA-256 `32b99fafc531eca2fdd3a81318f7bf49370d1e9c55aa1f4cbd802745a1f48b7c`;
- [source JSON receipt](../../evidence/runtime/state-storage-measurement-2026-09-18/source-json-receipt-005.json),
  SHA-256 `182dfcd99209ef76a96a63c27a4f1bd38be2295eaf402fe5bfdde0684af17f8e`.

The first profiler output, attempt 004, was superseded after review found that
its post-read check did not reject a writer that created a WAL during the scan.
Attempt 005 checks WAL, shared memory and rollback journals before and after,
records the schema and runtime, and groups raw documents as bytes. The earlier
files remain retained as a distinguishable attempt and do not support the
findings above.

### Full-copy `WITHOUT ROWID` prototype

The first layout candidate changed only the physical shape of
`derivation_rows`: it used `WITHOUT ROWID` with the existing
`PRIMARY KEY(generation_id, ordinal)` and
`UNIQUE(generation_id, table_name, record_key)`. The benchmark copied the full
database twice, analyzed and vacuumed both copies, rebuilt the candidate table,
and used deterministic gzip of each whole SQLite file as a backup-size proxy.
It did not claim that proxy was a Swingset checkpoint.

| Measure                              | Compact rowid baseline | `WITHOUT ROWID` |      Saving |
| ------------------------------------ | ---------------------: | --------------: | ----------: |
| Whole database                       |          4,859,154,432 |   4,716,625,920 | 142,528,512 |
| `derivation_rows`, table and indexes |          1,322,237,952 |   1,179,709,440 | 142,528,512 |
| Deterministic gzip proxy             |            499,507,625 |     484,737,920 |  14,769,705 |

The candidate removes the separate primary-key b-tree. Its table shrank by
6,422,528 bytes and its logical-identity index shrank by 1,712,128 bytes after
both layouts were vacuumed. Overall it saves 10.8% of the derivation-row object
and 2.9% of the compact database; the compressed proxy saves 3.0%. The table
rebuild took 8.42 seconds and its vacuum took 45.57 seconds on this one worker
run. Those timings are observations, not service levels.

Every gate passed. Both copies contained the exact 1,669,989-row stream and all
34,987 generation labels; every stored output digest and row count matched.
Integrity, foreign keys, both duplicate probes, deferred-label rejection,
immutability and history-fence behavior passed. The candidate kept direct
primary-key lookup for ordered generation reads and the unique index for
logical-key and artifact reads. Three warm samples showed no ordered-stream
regression and a faster full retention grouping scan. Generation counts were
about 20% slower by the three-sample medians, while point reads stayed in microseconds.
The artifact timing selected the largest non-artifact generation and returned
no rows, so only its query plan is useful. Repeat timing and an actual
checkpoint would be needed before an operating claim.

Retained output:

- [layout report](../../evidence/runtime/state-storage-measurement-2026-09-18/derivation-layout-report-002.json),
  SHA-256 `82ebcd5d93df040233383f5af9965b989aaae259b7382c523a776bb9683d15d1`;
- [layout receipt](../../evidence/runtime/state-storage-measurement-2026-09-18/derivation-layout-receipt-002.json),
  SHA-256 `3ec6c3d4da28a9c6c3026525f60f35ab4e21648b9b0217527bb281cc8c5f9c74`.

The first layout output, attempt 001, was superseded after review found that its
constraint probes accepted any SQLite error and that its compressed proxy was
not hash-bound to the inspected database bytes. Attempt 002 requires the exact
constraint errors, records full source and layout hashes, and binds each gzip
stream to its inspected input. It ran on host `swingset` with Python 3.12.13
and SQLite 3.50.4. The earlier files remain retained and do not support the
recommendation.

The benchmark tool's SHA-256 is
`e7a321e77f325affca68313f6ff9e41dc31c3fe49e28a07df81824252c6efe66`;
the JSON profiler's is
`f7a31758e55c07ff45f5f0b8d5ff7e70696cc25d06b3cc0ddb467b022b53d1ad`.
Twenty-one focused tool tests pass, along with the suite-classification tests,
Ruff and strict mypy for both tools.

### Recommendation, not a decision

Adopt `WITHOUT ROWID` when `derivation_rows` is next rebuilt. It preserves the
accepted interface and integrity rules, and the measured saving is real after a
fair vacuumed baseline. Do not add a special production migration solely from
this report: the saving is 2.9% of the file, production is held, and schema 32
already plans to replace this table. The owner should choose whether to put the
layout in an earlier standalone migration, apply it only to schema 32's row
reference table after a separate measurement, or keep the present layout.

For source JSON, benchmark exact compression before structural interning. Keep
`recipe_json` query fields available to SQLite, route full document reads
through one accessor, and verify every reconstructed byte and dependent digest.
That is a separate higher-impact design investigation; no JSON schema rewrite
is selected here.

### Decision and compression prototype, 2026-09-20

[D-0173](../../decisions/0173-defer-row-layout-to-schema32-and-prototype-json-compression.md)
resolved the row-layout recommendation without a standalone migration. Schema
32 must measure its actual reference table before adopting `WITHOUT ROWID`.
[D-0174](../../decisions/0174-benchmark-one-exact-source-json-compression-layout.md)
selects one bounded JSON prototype: compact A0 and A1 copies, with the
generation table retained as the relational anchor and four independent zlib-6
streams held in a one-to-one `WITHOUT ROWID` side table. SQL-visible recipe
facts remain on the anchor. Structural interning stays deferred.

The offline benchmark and 10 focused fixture tests are implemented. They prove
byte-exact reconstruction, stored lengths and SHA-256 values, generation and
input identities, extracted recipe values, one-to-one integrity, immutability,
authorized delete cascade, closed-source fencing and fail-closed decoding. A
malformed recipe is preserved byte for byte while the identity gate correctly
fails. The full 5 GB measurement has not run: the earlier disposable restore
was pruned, so this prototype still needs a fresh restore of the held
checkpoint. No production schema, migration or checkpoint result follows from
the fixture run.

Two unretained worker attempts stopped before measurement completed. Attempt
001 rejected unrelated tables that happened to use generic `manifest_json`,
`recipe_json` or `result_json` column names. Attempt 002 reached the candidate
rebuild, then exposed three triggers on other tables that query
`source_generations`; the temporary drop-and-rename gap made SQLite reject the
schema. The tool now limits its document-dependency check to objects that also
name `source_generations`, and it captures, removes and exactly recreates
external triggers and views around the anchor rebuild. The fixture includes
both regression shapes, and all 10 focused tests and seven suite-selection
tests pass. Failed scratch was removed. The sealed source still has SHA-256
`5840f97da69c42adb6e60132418c64c04505ae043a650723c84ff38644c64265` and
5,016,936,448 bytes.

### Full-copy A0/A1 source JSON compression benchmark, 2026-09-21

Attempt 003 ran the full benchmark on the worker against the same sealed
schema-29 restore and produced the size result below, but it failed one gate,
`query_outputs_match`. That failure was a defect in the benchmark, not in A1.
Two of the four measured reads have no `ORDER BY`, and A1 legitimately changes
which object serves them: `retained_source` moves from a full scan to an
ordinary index on `recipe_source`, and `snapshot` scans an anchor that is now
narrow. SQLite therefore returned the same rows in a different order, and the
tool compared an order-sensitive digest. Both reads were confirmed to return
identical row sets, 111 and 3 rows. The tool now records an order-independent
`output_multiset_sha256` beside `output_sha256` and marks each read as ordered
or unordered; a read whose SQL fixes the order must still match in that order.
Attempt 004 re-ran the corrected tool and passed all 20 gates with byte-identical
sizes. Attempt 003 is not retained.

Attempt 004 ran as root on host `swingset` with Python 3.12.13, SQLite 3.50.4
and zlib 1.3.2, under `nice -n 10` while production was parsing its backlog. It
copied the 5,016,936,448-byte source twice, compacted both copies the same way,
and rebuilt the candidate. It took 388.9 seconds and peaked at 14,362,401,160
bytes of scratch. The source was verified before and after: same SHA-256, same
size, no write-ahead log, shared-memory file or rollback journal. Retained
output:

- [compression receipt](../../evidence/runtime/state-storage-measurement-2026-09-18/source-json-compression-receipt-004.json),
  SHA-256 `e32d8deff7cf0cd5881adbfb87bb235149ea042efa8f33d0d165f9dfc42c72a7`.

The 65,573-byte full report stays in worker scratch under the evidence rules;
its SHA-256 is
`606b7c423b606b0eba40e169dbbc9408c4d7d9bc985818f36e1330c7899bf70d`. The
corrected tool's SHA-256 is
`9cb6080794997ad73f518ca64247e829d8dfe1d3fd854d92182f2c9885011cce`.

An independent read of the attempt 004 report on 2026-09-21 reconciled every
delta, per-object total and content digest and confirmed the result. It found
three gaps in the tool, none of which changes attempt 004: the read gate could
pass on empty result sets if a read parameter were NULL, nothing tied the
rebuild counters `rows_streamed` and `raw_document_bytes` to the content
profile, and the ANALYZE difference between layouts was unrecorded. The tool is
now v2: read parameters come from rows that have the value, every measured read
must return a row, a `candidate_build_matches_content` gate ties the rebuild
counters to the content profile, and the ANALYZE difference is a listed limit.
Its SHA-256 is
`663de161bc750da8e4ff00bc0c36c42a4c00894c40f61d9247fda2d549c2236b`. Attempt
004 stands as a v1 result; the next run uses v2.

| Measure                              | A0 compact baseline |            A1 |                Change |
| ------------------------------------ | ------------------: | ------------: | --------------------: |
| Whole database                       |       4,859,154,432 | 4,305,444,864 | −553,709,568 (−11.4%) |
| `source_generations` and its indexes |         730,222,592 |   176,508,928 | −553,713,664 (−75.8%) |
| Deterministic gzip proxy             |         499,507,387 |   570,988,752 |  +71,481,365 (+14.3%) |

The 32,189 generations hold 686,890,362 logical UTF-8 document bytes, which
became 116,539,106 bytes as independent zlib-6 streams, a ratio of 5.89. That
reproduces the earlier logical profile exactly. The ratio is not the storage
outcome: the document table is 155,774,976 bytes, so its keys, four byte
counts, four digests and page waste add about 39 MB, a quarter of the table.
All six anchor indexes are byte-identical between layouts, so the whole saving
comes from the tables. The candidate rebuild took
15.53 seconds and its vacuum 76.47 seconds; the baseline vacuum took 68.32
seconds. Those are single-run observations, not service levels.

Every gate passed. Both copies reproduced the source's exact document,
metadata and extracted-field streams; every generation identity and input
fingerprint recomputed; one-to-one integrity, foreign keys, `integrity_check`,
immutability on both tables, the deferred document-delete rejection, the A1
schema and trigger shape and the unchanged external dependents all held. Both
layouts had an empty free list, and each gzip proxy was bound by hash and
identity to the database bytes it describes.

Reads improved where A1 puts a real column behind an index. Best of three
samples: `retained_source` went from 0.148316 to 0.000089 seconds because a
full scan calling `json_extract` became a plain index search; `snapshot` went
from 0.138430 to 0.002325 seconds because the scan now reads an 11 MB anchor
instead of a 720 MB one; `unit_history` was unchanged at 8 microseconds. The
one regression is the full-document point read, 0.000022 to 0.000064 seconds,
which is the cost of four zlib decodes.

The result the benchmark did not settle is the durable-history cost. The
deterministic gzip proxy moved the other way: pre-compressing each document
removes redundancy that a whole-file compressor was already exploiting, so the
candidate's archive is 14.3% larger even though its database file is 11.4%
smaller. That figure describes gzip alone. DEFLATE looks back only 32 KiB, so a
codec with a long window would find more of A0's cross-generation repetition;
the direction is likely to hold but the size is not transferable. That proxy is
also not a Swingset checkpoint. Checkpoints are written by
`connection.backup()` and copied without compression, so the proxy describes a
hypothetical compressed archive rather than what the worker stores today; what
a real checkpoint costs under A1 is unmeasured.

[D-0178](../../decisions/0178-keep-inline-source-json-and-hold-a1-until-the-size-trigger.md)
records the outcome: the inline layout stays, A1 is not designed into a
production schema, and it is the first candidate to revisit if the state-size
trigger in `AGENTS.md` fires. That closes step 2a.

## Known local test failure

`tests/publish/test_publication_controls.py::test_process_death_retains_active_pause_drain_until_recovery`
fails on this machine before and after the bounded-state work and touches no
retention code. The test starts a child process and replaces its `PYTHONPATH`
with the repository's `src` and its own test directory. Under `nix develop` the
pytest process has a `PYTHONPATH` of its own, set by the dev shell to several
Python 3.13 store paths, so the parent sees those distributions installed and
the child does not. The two compute different runtime identities and input
bundle hashes, and the child stops before printing its handshake line. Running
the test with an empty `PYTHONPATH` inside the shell, as in
`nix develop -c env PYTHONPATH=/tmp/empty uv run pytest <the test>`, makes it
pass. This is an environment problem and is not fixed here.
