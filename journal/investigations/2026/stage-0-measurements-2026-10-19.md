# Stage 0 measurements: where re-derivation time goes and what a proof costs

Date: 2026-10-19  
Type: Investigation  
Topic: Derivation identity, Postgres migration  
Related plan or decision: [Stage 0](../../../docs/plans/postgres-migration/stage-0.md), [D-0264](../../decisions/0264-declared-transform-versions-and-differential-proofs-decide-re-derivation.md)

## Worker measurements (items 1–4)

**In short:** a derive tick spends most of its time deciding which units are
out of date, not running them. About 53% of its CPU goes to computing
dependency fingerprints (`desired()` in `state/derivations.py`), and nearly
half of the tick is spent picking the next unit. Transforms and their row
writes take about a fifth of the tick; commits take 1.5%. A whole-graph
proof on a clone takes about 1 hour of work at one process, and about
35 minutes at two (estimate). That is well under D-0264's guess of 2 to 4
hours. Every current unit the proof touched gave its stored output: 1,485 of
1,486 project and link units (the one exception is explained below) and all
35,647 accepted parse units. Old code from an accepted bundle runs as-is.

**Setup.** Measured 2026-10-19 between 16:32 and 17:05 UTC on the worker VM
(4 vCPU aarch64, Nix Python 3.12.13), with the deployed tree
(`dvy0vkgq…-source`, input bundle `e80f675d…`). Everything except the live
profile ran on a reflink clone of production's state, taken under an
operator hold that lasted 4 seconds (16:48:01.5 to 16:48:05.5). No loop
was running then, and production had no `-wal` file, so the clone was the
checkpointed database alone. Runs used `nice -n 19 ionice -c3`, at most
two processes. Production kept running throughout (see the end of this
section).

### Item 1: where a unit's time goes

**What production's own records say.** `work_attempts` holds the start and
finish of each unit's body. The finish is written just before the unit's
commit, so a body includes its transform, its row writes and its completion,
but not the commit or the choice of the next unit. Since 2026-10-05:

| Unit kind                  |  Bodies | Median s |  p90 s |  p99 s |
| -------------------------- | ------: | -------: | -----: | -----: |
| parse/wsdc_registry.dancer |  59,014 |    0.003 |  0.005 |  0.016 |
| parse/scoringdance.round   |  11,135 |    0.014 |  0.044 |  0.101 |
| parse/eepro.round          |   1,039 |    0.018 |  0.102 |  0.252 |
| parse/wdr.rounds           |      27 |    0.538 |  1.211 |  1.731 |
| project/dancer             | 205,316 |    0.018 |  0.027 |  0.043 |
| project/event              |  22,003 |    0.019 |  0.186 |  1.201 |
| project/resolution         |   7,469 |    2.245 |  2.375 |  2.899 |
| project/source_index       |   5,632 |    0.306 |  0.359 |  0.644 |
| project/source_event       |   4,649 |    0.151 |  0.330 |  0.582 |
| project/year               |   2,577 |    3.027 |  4.433 |  6.946 |
| project/inventory          |      25 |   13.916 | 16.322 | 19.282 |
| link/event                 |  45,882 |    0.244 |  1.746 |  3.807 |

Other parse kinds had a median under 0.11 s. The same period's derive
ticks:

| Part of the tick, 1,282 ticks with work, 408,084 units                | Hours | Share |
| --------------------------------------------------------------------- | ----: | ----: |
| Unit bodies (transform, row writes, completion)                       | 17.69 |   21% |
| Between units: commit, finishing, choosing and admitting the next one | 47.32 |   57% |
| Before the first unit (start-up, recovery, readiness)                 |  9.54 |   11% |
| After the last unit                                                   |  8.89 |   11% |
| **Whole ticks**                                                       | 83.45 |  100% |

That is 0.74 s of tick per unit, while the median body is 0.02 s. Since
2026-10-17 it is 0.82 s per unit, with bodies at 18%.

**A live profile.** `py-spy` (sampling at 50 Hz, `--nonblocking`, so it
reads the process and never pauses it) recorded four live derive ticks,
16:32 to 16:47. They ran 347 units in 660 s (1.9 s per unit): 307 link/event
and 34 project/year re-runs that cutoff kept, and 3 new project/dancer
generations. 30,896 samples:

| Phase of the tick                                    | Share | Mostly                              |
| ---------------------------------------------------- | ----: | ----------------------------------- |
| Choosing the next unit (`iter_work` currency checks) | 47.1% | `desired()` fingerprints 36.1%      |
| Unit body                                            | 40.7% | see below                           |
| Staged completion (`finish_building`)                |  7.2% | `desired()` again, in `_fence` 6.1% |
| Admission (lease, pause check, capture, attempt)     |  3.9% | `desired()` 3.5%                    |
| Outside units                                        |  1.1% |                                     |

By what the code was doing, across all phases:

| Work                                                        | Share |
| ----------------------------------------------------------- | ----: |
| Dependency walk: `desired()` fingerprints                   | 53.0% |
| Dependency walk: currency checks and certificates           |  9.5% |
| Link compute (`resolve_event`)                              | 14.8% |
| Output label and comparison (`output_rows`, `_fingerprint`) |  9.7% |
| Link input loading (`load_linking_snapshot`)                |  5.0% |
| Link row writes (`commit_resolution`)                       |  2.6% |
| Commit (`Database._commit`, which waits for fsync)          |  1.5% |
| Other                                                       |  3.9% |

The hottest single lines are `json.encoder.iterencode` (canonical JSON for
hashing, 20% in the first tick) and `derivation_dependencies._scopes`
(15%). The service log agrees: a 240 s tick used 3 min 59.7 s of CPU, so
derive is bound by one CPU, not by the disk.

**Per unit on the clone.** Item 2's runs wrapped the transform's functions
with timers and timed every SQL statement by its first word. Mean seconds
per unit, without the two extra labels that only verify computes:

| Unit kind            | Mean s | Python | SQL reads | SQL writes |
| -------------------- | -----: | -----: | --------: | ---------: |
| project/dancer       |  0.023 |  0.014 |     0.008 |      0.001 |
| project/event        |  0.037 |  0.015 |     0.020 |      0.002 |
| link/event           |  0.430 |  0.150 |     0.264 |      0.016 |
| project/resolution   |  2.120 |  0.367 |     1.752 |      0.001 |
| project/source_index |  0.432 |  0.296 |     0.133 |      0.003 |
| project/source_event |  0.190 |  0.082 |     0.100 |      0.009 |
| project/year         |  2.853 |  0.642 |     2.179 |      0.032 |

Weighted by the graph's unit counts, a proof's time is 61% SQL reads, 36%
Python and 3% SQL writes.

**What this decides.** The stage 0 plan asked whether transactions and
bookkeeping, not parsing, explain about 0.5 s per unit. Bookkeeping does;
transactions do not. Commits are 1.5% and row writes about 3%, so batching
commits would gain little. A parallel executor would help only as far as
the selection and fingerprint work splits across processes. The largest
lever is making `desired()` cheaper or computing it less often: production
computes it for each candidate while choosing, again at admission, again in
`_fence` and again when a staged unit completes. This is a measurement, not
a decision.

### Item 2: verify-style run on the clone

`state/verify.py`'s `verify_unit` runs a unit's real write in one
transaction, labels the output and rolls the write back. The sample was
stratified by kind: 1,000 project/dancer, 200 project/event, 200 link/event,
20 of each other project kind (all of them where fewer), 1,491 in all.

| Unit kind            | Units | Equal to stored | Median s | p90 s | p99 s |
| -------------------- | ----: | --------------: | -------: | ----: | ----: |
| project/dancer       | 1,000 |           1,000 |    0.021 | 0.034 | 0.055 |
| project/event        |   200 |             200 |    0.016 | 0.090 | 0.605 |
| link/event           |   200 |             200 |    0.580 | 1.508 | 3.465 |
| project/resolution   |    20 |              20 |    2.050 | 2.283 | 2.614 |
| project/source_index |    20 |              20 |    0.433 | 0.459 | 0.485 |
| project/source_event |    20 |              20 |    0.160 | 0.526 | 0.592 |
| project/year         |    20 |              20 |    2.910 | 3.513 | 4.727 |
| project/mapping      |     4 |               4 |    2.639 | 3.201 | 3.201 |
| project/calendar     |     2 |               1 |        — |     — |     — |

- **Equal output: 1,485 of 1,486** current units. Five more (two build
  units, map, history, inventory) were not current, so verify skips them.
- **The one difference is not a defect of the transform.** For
  `project/calendar/wsdc` the live rows differ from the stored generation
  both before and after the run. `process_unit` does not re-run a current
  calendar, because the inventory later enriches its rows on purpose. So
  verify compares rows the inventory changed. A proof runner must leave
  current calendars out or compare them another way.
- **No failures and no nondeterminism.** The two runs below gave the same
  state and label for all 1,491 units.
- **Speed.** One process: 1,491 units in 327.6 s (4.6 units a second). Two
  processes, each on its own reflink clone: 187.7 s (7.9 a second), 1.75
  times faster, while production's derive tick ran beside them.
- **Memory.** Peak 828 MB in one process, reached at the inventory unit's
  currency check; 218 MB for the other process of the pair.
- **The clone is sound.** `PRAGMA quick_check` on a clone returned `ok` in
  197 s. The clone captured the same input bundle digest as production
  (`e80f675d…`), and every sampled project and link unit was current, as
  on production.
- **Verify does extra work a proof does not need.** It labels the live
  output before and after the run. For link/event that is 47% of its time
  (0.38 of 0.82 s).

**Whole-graph proof, estimated** from these means and the graph's 36,543
current units (29,216 dancer, 2,952 event, 2,952 link, 527 resolution, 479
source_event, 387 source_index, 21 year, 4 mapping, 2 calendar):

| Way of running                            | Project and link | Parse and extract | Total   |
| ----------------------------------------- | ---------------: | ----------------: | ------- |
| `verify_unit` as written, 1 process       |           1.31 h |            ~4 min | ~1.4 h  |
| Without verify's extra labels, 1 process  |           0.97 h |            ~4 min | ~1.0 h  |
| Without extra labels, 2 processes (×1.75) |           0.55 h |             2 min | ~35 min |

These are estimates: inventory (about 14 s) and the build were not run, and
3 or 4 processes were not tried. A re-derivation of the same units on
production takes about 7.5 hours of ticks at 0.74 s per unit.

### Item 3: extract and parse over every stored input

**Accepted parse units.** For each of the 35,647 source units with an
accepted generation, the run read the snapshot's body, extracted, parsed,
declared and inspected it with the current code. It compared the manifest,
`result_json`, `report_json` and contract version with the accepted
generation, which is the comparison parse cutoff makes. The full set took
72 s at two processes, so no sampling was needed.

| Page kind             |      Units |      Equal | Different | Median ms | p90 ms | Max ms |
| --------------------- | ---------: | ---------: | --------: | --------: | -----: | -----: |
| wsdc_registry.dancer  |     29,214 |     29,214 |         0 |       0.6 |    1.5 |     41 |
| scoringdance.round    |      5,548 |      5,548 |         0 |      10.2 |   37.3 |    178 |
| eepro.round           |        472 |        472 |         0 |      16.0 |  110.9 |    295 |
| scoringdance.event    |        319 |        319 |         0 |       2.4 |    4.4 |     37 |
| eepro.autoindex       |         77 |         77 |         0 |       1.5 |    4.1 |     32 |
| wdr.rounds            |         14 |         14 |         0 |     304.8 |  527.5 |    735 |
| 3 kinds with one unit |          3 |          3 |         0 |         — |      — |    178 |
| **Total**             | **35,647** | **35,647** |     **0** |  mean 4.0 |        |        |

Unit time totalled 143 s: extract 50%, declare and inspect 24%, serializing
11%, reading the body 6%, database reads 5%, parse 3%. Peak memory was
108 MB per process. Here the wdr.rounds report included the
`wdr_preserves_rows` guard, computed from the clone the way `stage()` does,
and still matched.

**Every snapshot's extract.** All 40,129 snapshots with a body were
extracted again and compared with `snapshots.extract_sha256` (50 s at two
processes):

| Result                                             | Snapshots |
| -------------------------------------------------- | --------: |
| Same extract digest                                |    39,982 |
| Different, stored under an older `EXTRACT_VERSION` |       133 |
| No stored extract, and extraction still fails      |        14 |

The 133 are scoringdance.round snapshots extracted under version 3 or 4
(today 5) that no later parse needed again. The 14 are the snapshots
without an extract: 11 raise the same `ExtractError` as before, and 3 are
`registry_crosscheck` pages, a kind the code no longer has (parked under
D-0182).

### Item 4: old code side by side

Two accepted bundles' `runtime/` trees were imported in separate processes
(`PYTHONPATH` set to the tree, today's venv) and run over 207 snapshots
across 9 page kinds (50 registry, 50 scoringdance.round, 40 eepro.round, 30
scoringdance.event, 20 eepro.autoindex, all 14 wdr.rounds, the 3 one-unit
kinds). Output was compared with today's stored generations.

| Bundle (captured)              | Imports | Equal to stored | Different | Mean ms per unit |
| ------------------------------ | ------- | --------------: | --------: | ---------------: |
| `effc2008…` (2026-10-15 22:48) | as-is   |             207 |         0 |               35 |
| `d17e4562…` (2026-09-29 19:30) | as-is   |             143 |        64 |               36 |

- **Old code runs as-is.** Both trees imported with no error. Their
  `uv.lock` and `environment.json` are byte-identical to today's, so there
  was no dependency drift to find in this window. Import took 0.06 s.
  Per-kind times match today's code (scoringdance.round median 15 ms,
  wdr.rounds 283 ms; the mean is high because the sample favours slow kinds).
- **`effc2008…` shows the waste stage 0 removes.** Every one of its 40
  extract and parse scope digests differs from the next bundle's
  (`0606ea9e…`, 2026-10-17), so all parse units became out of date. Yet its
  code gives today's stored bytes for all 207 sampled units.
- **`d17e4562…` shows what a real change looks like.** Of 64 differences,
  61 are only labels: 49 scoringdance.round units carry `extract_version` 4
  instead of 5, 12 wdr.rounds units an older `parser_version`, each with an
  older contract version. Three differ in content: one scoringdance.round
  report (state, failures, guards) and two wdr.rounds results (observations).
- **Limit:** old code was compared with today's stored output, not with what
  that era stored, because older generations are superseded.

### Production while measuring

| Check                                    | Result                                                                 |
| ---------------------------------------- | ---------------------------------------------------------------------- |
| Operator hold                            | 16:48:01.503 to 16:48:05.454 UTC, 3.95 s; no loop unit was active      |
| Derive and acquire runs, checked 5 times | none failed; derive ticks completed 119, 102, 126, 147 units as usual  |
| `ALERTS.log`                             | 947 lines before and after; no new line                                |
| Disk (`swingset-disk`)                   | 127.5 GB used before; 128.3 GB at most during; 127.8 GB after clean-up |
| Writes to `/var/lib/swingset`            | only the hold file, removed after 4 s                                  |

The clones and all scratch under `/var/tmp/swingset-stage0m/` were deleted
at 17:05 UTC. The runner scripts and the hold receipt stayed in session
scratch and are not retained, per AGENTS.md.

## Mac measurements (items 5–6)

Measured 2026-10-19 on the Mac in Docker (OrbStack), at most 2 CPUs per
container. Production was only read: the newest accepted input bundle
(`inputs/e80f675d…`), 303 sampled parse generations with their bodies and
extracts, and table statistics. The repository's `src/` was byte-identical to
the deployed tree: every scope digest matched.

### Item 5: labels, environment and output parity

**Setup.** `python:3.12.13-slim` (Debian, GCC 14.2.0, glibc 2.41) plus
`uv sync --frozen --no-dev`, built once for linux/arm64 (native) and once for
linux/amd64 (emulated by Rosetta). Production runs Nix's Python 3.12.13 built
with GCC 14.3.0 on arm64.

**Labels and scope digests.** These match everywhere, because they are read
from source bytes, not from the machine.

| Value                                                                                            | arm64 container       | amd64 container       |
| ------------------------------------------------------------------------------------------------ | --------------------- | --------------------- |
| Declared versions by import (`versions()`): 20 extract, 20 parser, projector 20, linker 9        | same as production    | same as production    |
| Declared versions by AST over the captured bytes (stage 0's reader), all 51 scopes               | same as production    | same as production    |
| `recipe_scope.manifest` over production's captured files vs its stored `scopes.json` (51 scopes) | equal                 | equal                 |
| `recipe/env` digest                                                                              | differs (`012f1c4c…`) | differs (`74d1fb95…`) |

**Why `recipe/env` differs.** Production's is `efebb172…`.

| Field in `environment.json`           | arm64 container                                   | amd64 container                               |
| ------------------------------------- | ------------------------------------------------- | --------------------------------------------- |
| Python version, implementation        | same (3.12.13, cpython)                           | same                                          |
| ABI                                   | same                                              | differs (`x86_64` instead of `aarch64`)       |
| Compiler                              | differs (GCC 14.2.0 vs 14.3.0)                    | differs                                       |
| Interpreter binary hash               | differs (Debian build, not Nix)                   | differs                                       |
| Wheel RECORD hashes, 24 distributions | 19 same; 5 differ only in console-script shebangs | 12 same; 7 compiled wheels and the 5 shebangs |

The five shebang cases are httpx, huggingface-hub, idna, numpy and tqdm. A
RECORD lists the hash of each `bin/` script, and the script's first line names
the venv's interpreter. Building the venv at `/var/lib/swingset/venv` did not
fix it: production's line says `python3`, the container's `python`. The amd64
compiled wheels are duckdb, hf-xet, pyarrow, pyyaml, rapidfuzz, scipy and
selectolax. So today's environment digest can never match across machines,
even on the same CPU type. D-0263's simpler identity (lockfile, Python
version, CPU type) would match the arm64 container and not the amd64 one.

**Output parity on production data.** 303 accepted (or, for shadow kinds,
desired) parse generations, chosen by hash across 14 page kinds. Each was
re-extracted from its stored body and re-parsed with the current code. The
check compares the extract digest with the stored one, and `result_json`,
`report_json` and the contract version with the stored generation.

| Page kind                | Units on production | Sampled | Same as production (arm64 / amd64) | arm64 = amd64 |
| ------------------------ | ------------------: | ------: | ---------------------------------- | ------------- |
| wsdc_registry.dancer     |              29,216 |     100 | 100 / 100                          | 100           |
| scoringdance.round       |               5,574 |      50 | 50 / 50                            | 50            |
| eepro.round              |                 481 |      40 | 40 / 40                            | 40            |
| scoringdance.event       |                 394 |      25 | 25 / 25                            | 25            |
| wsdc_calendar.events     |                 143 |      14 | 14 / 14                            | 14            |
| eepro.autoindex          |                  77 |      14 | 14 / 14                            | 14            |
| swingdancecouncil.events |                  37 |      14 | 14 / 14                            | 14            |
| wsdc_newsletter.events   |                  28 |      14 | 14 / 14                            | 14            |
| wdr.awards               |                  14 |      14 | 14 / 14                            | 14            |
| wdr.rounds               |                  14 |      14 | 14 / 14 (one guard left out)       | 14            |
| 4 kinds with one unit    |                   4 |       4 | 4 / 4                              | 4             |
| **Total**                |          **35,982** | **303** | **303 / 303**                      | **303**       |

The four one-unit kinds are eepro.index, scoringdance.recent,
scoringdance.sitemap and wsdc_newsletter.index. For wdr.rounds, `stage()`
adds a `non_authoritative_row_loss` guard computed from stored rows, which a
pure re-run cannot see. It was removed from the stored report before comparing.
Six registered kinds have no units yet (dcn.archive, dcn.event, the three
steprightsolutions kinds, swingdirector.round), so pypdf was not exercised.
Time: 6.0 s on arm64 (about 20 ms per unit, one process), 11.5 s emulated.

**Project, link and build on fixtures.** 12 test files (scoring, link,
project, registry, real fixtures, builder, partitions, verify) ran in both
containers: 149 tests passed in each. The 90 SQLite databases they left behind
were compared. Generation labels (`output_digest`, `row_count`), payload
digests, row references and source generations were identical in 89. The one
exception differed only in random candidate ids (`uuid4`) and the temp path;
its `content_hash` values were equal. All 438 parquet files were byte-identical.
The other raw differences were in bookkeeping tables that hold
fingerprints, generation ids, run times or random ids, because `recipe/env`
differs (checked by table name, not row by row). No canonical table
(entries, placements, identity links, link candidates and the rest)
differed, and the databases held 6,160 floating-point values.

**Library probes** (the same inputs on both architectures):

| Probe                                                      | Result                                   |
| ---------------------------------------------------------- | ---------------------------------------- |
| rapidfuzz ratio, token sort, WRatio, Jaro-Winkler, extract | identical                                |
| scipy `linear_sum_assignment`, 200 random matrices         | identical                                |
| Python float `repr` and `format`, `sum`, `math.fsum`       | identical                                |
| pyarrow parquet bytes (zstd), selectolax text              | identical                                |
| duckdb `sum`, `avg`, `min`, `max` of doubles               | identical                                |
| duckdb `stddev`                                            | last digit differs (`…57965` vs `…5797`) |
| `math.log` and `np.log`, 301,000 inputs                    | 29 differ by one unit in the last place  |

Swingset uses no `stddev`. It does use `-np.log(score)` as the cost in
`link/assign.py`. Only the chosen assignment is stored, so a one-ulp change
matters only if two assignments tie to within about 1e-16. No case appeared
in the fixtures (unverified at production scale).

**What this means for stage 0.** In this sample the code gave the same bytes
on arm64 and emulated amd64. That does not show a real AMD64 server gives the
same. Rosetta reports x86-64-v2 plus FMA3 to numpy, with no AVX or AVX2, and
numpy and duckdb choose code paths by CPU feature. `log` already differs in
the last bit between the two platforms' C libraries. So rule 4 stands: the
server needs its own proof, in its own image. An arm64 Linux container on the
Mac gave production's outputs for this sample. It could prove for today's
worker if the environment rule ignores the interpreter binary and shebangs.
That would need a project and link check on production units first, which
measurement 2 does on the worker.

### Item 6: log volume of rolled-back proof writes on Postgres

**Method.** PostgreSQL 18.6 (arm64, data checksums on by default) with the
cutover plan's settings: `shared_buffers=2GB`, `max_wal_size=4GB`,
`min_wal_size=1GB`, `wal_level=replica`. Autovacuum was off so each run could
be measured alone. The tables copy production's indexes, row counts and row
widths, measured read-only:

| Table                   | Rows (production) | Mean bytes per row (production) |
| ----------------------- | ----------------: | ------------------------------: |
| derivation_scopes       |            36,545 |                             242 |
| derivation_equivalences |           379,290 |                             351 |
| derivation_reads (link) |           369,067 |                 61 (key length) |
| entries                 |           199,768 |                             317 |
| judges                  |            11,185 |                             213 |
| placements              |            39,206 |                             408 |
| snapshots               |            40,129 |                           1,427 |
| watches                 |            35,841 |                             466 |
| source_units            |            35,647 |                             220 |
| source_equivalences     |           100,962 |                           1,263 |

Each simulated unit makes the writes that the code makes when the output is
the same as stored (the cutoff path):

- **Every derivation unit:** two updates of its `derivation_scopes` row, one
  new `derivation_equivalences` row (five indexes) and a recipe row that
  already exists.
- **A link unit also:** deletes and re-inserts its read set (125 keys on
  average) and updates every entry, judge and placement of its event (once
  per row, and placements twice). Each unit used its event's real counts.
  Postgres writes a new row version even when the values do not change.
- **A parse unit, if written:** one `source_equivalences` row, one
  `source_units` update, two `snapshots` updates and one `watches` update.

A checkpoint makes the next change to each page log a whole 8 KB copy of it
(a full-page image). A proof of 2 to 4 hours would see 24 to 48 checkpoints at
the default 5 minutes. The runs simulate this with a `CHECKPOINT` every 1,000
units (36 in all), with one every 3,000, and with none.

**Whole-graph derivation proof**, 36,543 units, every transaction rolled back:

| Settings                             | Log written | Per unit | Full-page images | Later `VACUUM` |
| ------------------------------------ | ----------: | -------: | ---------------: | -------------: |
| full-page writes on, no checkpoints  |     0.93 GB |  25.5 KB |           53,924 |        0.02 GB |
| full-page writes on, 12 checkpoints  |     2.19 GB |  59.8 KB |          242,734 |        0.50 GB |
| full-page writes on, 36 checkpoints  |     3.24 GB |  88.8 KB |          393,547 |        0.50 GB |
| same, `wal_compression=lz4`          |     2.37 GB |  64.9 KB |          393,549 |        0.26 GB |
| same, `wal_compression=zstd`         |     1.68 GB |  46.1 KB |          400,460 |        0.16 GB |
| full-page writes off, 36 checkpoints |     0.56 GB |  15.4 KB |                0 |        0.02 GB |

**The same writes committed**, as a re-derivation with cutoff does today:

| Settings                             | Derivation (36,543 units) | Parse (35,647 units) |
| ------------------------------------ | ------------------------: | -------------------: |
| full-page writes on, no checkpoints  |                   0.86 GB |              0.25 GB |
| full-page writes on, 36 checkpoints  |                   2.86 GB |              1.37 GB |
| same, `wal_compression=lz4`          |                 (not run) |              0.88 GB |
| full-page writes off, 36 checkpoints |                   0.49 GB |            (not run) |

Parse writes rolled back, 35 checkpoints: 2.30 GB with full-page writes on,
0.22 GB with them off.

**Rolling back saves nothing.** A rolled-back transaction logs every change
it made, then an abort record. It also leaves its row versions on the page
until a vacuum. Later updates on that page then cannot stay on the page (no
HOT update), so they add index entries. A breakdown of 4,000 parse units
(`pg_waldump --stats`) showed 10,340 plain updates against 5,748 when committed,
and 67,649 index leaf inserts against 45,270. Rolled back cost 16% more in that
sample and 68% more over all parse units. About 90% of the bytes were
full-page images. Wall time for the writes alone was 15 to 33 s rolled back
and 67 to 104 s committed, so the writes are not what makes a proof slow.

**Estimates** (marked as such; they rest on the simulation above):

| Case                                                                             | Log written (raw)                                         |
| -------------------------------------------------------------------------------- | --------------------------------------------------------- |
| Whole-graph proof, derivation units only (the plan's parse proof writes nothing) | 2.2–3.2 GB, plus 0.5 GB of later vacuum                   |
| Same, if parse units also ran their writes                                       | 4.5–5.5 GB                                                |
| Full re-derivation, committed, derivation and parse                              | about 4.2 GB, plus 0.5 GB vacuum                          |
| Archive from `archive_timeout=15min` alone                                       | at most 96 segments a day, 1.5 GB raw, mostly zero-filled |

During a proof the log grows by roughly 13 to 27 MB a minute, so 16 MB
segments fill in about a minute and the 15-minute timeout adds nothing. If
the proof ran in the production cluster, everything above would be archived.
zstd shrank this log to about half, so an archiver that compresses would
store roughly 1.5 GB per proof (estimate). The plan expects about nine
whole-graph proofs in three weeks, so 20 to 40 GB of archive a month (estimate).

**What this means for stage 0.** Run proofs on a separate scratch cluster
(the drill's restore) with `archive_mode=off`. Then the log is local, reused
disk, bounded by `max_wal_size`, and nothing reaches the archive. Because a
crash there only means restoring again, that cluster can run with
`full_page_writes=off` (about 6× less log) or unlogged tables (almost none).
Never run a proof as a scratch database inside the production cluster. Its
log, including full-page images, would join the archive and the 15-minute
recovery point.

**Limits of the method.**

- The rows are synthetic, with production's counts, widths and indexes.
  The writes come from reading the cutoff code path, not from tracing a
  real run.
- Left out: findings rewrites, new dependency sets, `bump_replay`,
  `pending_work`, TOAST, and full-page images caused by setting hint bits
  on reads. The tables were frozen by `VACUUM` before each run; a fresh
  restore without one would log more.
- One process in random unit order. Real proofs run 3 to 8 processes and
  may group units by kind, which changes how often a page is reused
  between checkpoints.
- Checkpoints were forced by unit count, not by time.
- The archiver's compression was not measured; the zstd figure stands in.

### Evidence and reproduction

The scripts (`labels.py`, `parity.py`, `sample_prod.py`, `shapes_prod.py`,
`walsim.py`, the library probes) and their raw output ran from session
scratch and are not retained, per AGENTS.md. Production reads were read-only
SQLite connections (`mode=ro`) as the `swingset` user at `nice 19`. Each took
under 6 s. Containers, images, the network and the build cache were removed
afterwards.
