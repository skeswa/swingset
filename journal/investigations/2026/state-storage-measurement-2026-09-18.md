# Measuring where state storage goes

Date: 2026-09-18 UTC
Status: Tool implemented and tested; the measurement ran on 2026-09-18 (see
"Measurement run" below). Step 1's decision is proposed in
[D-0166](../../decisions/0166-hold-interning-back-until-rows-repeat-and-cut-indexes-first.md).
Decision: [D-0119](../../decisions/0119-bound-state-by-interning-and-one-closure.md)
(plan direction), [D-0127](../../decisions/0127-measure-state-storage-read-only-on-a-copy.md)
(tool design, proposed),
[D-0132](../../decisions/0132-time-a-held-checkpoint-by-restoring-it-first.md)
(backup timing order, proposed),
[D-0135](../../decisions/0135-fence-every-path-the-measurement-writes-and-exit-on-the-gates.md)
(write fence and gate-driven exit status, proposed)

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
