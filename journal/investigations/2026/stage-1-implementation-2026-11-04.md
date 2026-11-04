# Stage 1: the local PostgreSQL port

Date: 2026-11-04 to 2026-11-05 UTC  
Type: Local outcome  
Topic: PostgreSQL migration  
Related plan: [stage 1](../../../docs/plans/postgres-migration/6-stage-1.md)

## Outcome

Stages 0 and 1 passed their local gates on 2026-11-05. The complete regression
suite, developer feedback gate and real-sample empty-restore proof passed.
Next is stage 2's full-size server rehearsal.
The pipeline uses native PostgreSQL SQL and psycopg on `postgres-migration`.
SQLite remains the frozen import format. Production still runs its unchanged
SQLite worker; this session did not deploy, push or publish a dataset remotely.

## Preflight and runtime

The [read-only preflight](../../evidence/runtime/postgres-stage1-2026-11-04/preflight.json)
found PostgreSQL 17.11 and 842 GB free on Rhuidean. Its ext4 disk lacks
reflinks, so verification uses independent scratch databases. No server storage,
network or application configuration changed. Local work kept the Mac awake,
stopped other projects' containers, and kept free disk above 80 GiB and combined local container working memory
below 7 GiB. TEST and the dedicated physical profile each have a 2 GiB
limit; DEV has 4 GiB. After checked cleanup, all 13 original project containers
were restored and verified running, with every configured health check healthy.
The three local Postgres services remain warm; the image builder and all proof
jobs are stopped.

Development pins PostgreSQL 17.11 and Python 3.12.16, with native ARM64 test
and AMD64 worker images. Mise replaces Nix on this bookmark. The worker
cannot create databases or use owner credentials; backup checking uses a
separate owner job. The [development guide](../../../docs/guides/development.md)
records the maintained commands, offline seed, replay and environment guards.

## Engine, import and recovery

- Native statements pass unchanged. Rows preserve integer and name access;
  duplicate result labels are rejected. Readers use repeatable snapshots,
  and writer advisory locks survive commits on their owning connection.
  Deadline expiry rolls back; lost commit acknowledgement closes the
  connection and reports uncertainty without retrying an outside effect.
- Ordinary opens check schema identity. Explicit owner migrations acquire
  state, control and writer locks, settle stale admissions, preserve uncertain
  publication and refuse downgrades before changing domain state.
- The catalog has 141 source tables plus destination environment identity,
  809 triggers, 148 explicit indexes and one view. Parity compares structures
  and trigger bodies. The importer streams COPY and grants worker access
  only after independent typed hashes match every source table.
- The 141 source tables have explicit rules: 67 history, 36 rebuilt and
  38 bookkeeping, with row filters for mixed tables. History references
  have a transitive closure check. Backup captures one database
  snapshot with its files and publication metadata. Restore preserves
  history before allowing the documented normal anchor transitions.

Selected derivation receipts, including unpublished units, stay in history
([D-0272](../../decisions/0272-retain-selected-derivation-provenance.md)).
The empty cache must pass ordinary replay before downstream work can use it.
Without that rule, rebuilding the sample changed processing timestamps and
added 32,539 false changelog rows. Rendering now retains the original cutoff,
run and local-page verification time when the complete recipe, dependencies,
parent and health trigger match
([D-0273](../../decisions/0273-capture-build-rendering-inputs.md)). Changed
inputs get fresh rendering inputs; health cadence and attempt receipts still
use the actual clock.

The real sample also caught a preflight closure omission: the sampler retained
predecessor receipts but omitted their evicted output packs. Publication's
mandatory backup refused before any local FixtureIO head changed. Preflight
now follows predecessor chains with a visited set. A focused regression proves
that removing an evicted predecessor pack rejects the import. A bounded
read-only file capture added the exact 1,500 frozen-ledger packs (1,077,748
stored bytes), checked their raw and stored hashes, and left SQLite unchanged.
The final proof starts from a fresh import of that augmented artifact.

The first real pending-candidate restore matched every domain Parquet but
changed coverage and changelog. A retained `recipe/runtime` absence token was
in the original build recipe and missing after restore. Pipeline accepted
inputs now remain mutable history anchors; audit inputs and `recipe/env`
still reset ([D-0274](../../decisions/0274-retain-semantic-input-tombstones.md)).
Normal input acceptance creates and advances the actual tokens. Restore neither
guesses missing keys nor ignores recipe differences. The regression preserves
all 21 files after both published and genuinely pending-candidate restores.

The final post-rebuild audit exposed two existing mutable anchors: normal
object recording advances its garbage-collection clock, and naming the fresh
candidate moves a release manifest's candidate pointer. Import compares every
source field; initial restore compares every captured history field exactly.
The post-rebuild audit requires known object clocks to move forward, preserves unknown clocks, and
keeps object keys, sizes, manifest bytes, creation time, verdict and original
build receipts exact. Candidate naming may change only its documented pointer
([D-0275](../../decisions/0275-audit-object-gc-clocks-as-monotonic-anchors.md)).
Negative regressions reject clock rewinds and changes to immutable fields.
No stored timestamp, manifest or backup receipt was normalized or restamped.

## Sample proof

The final source contains 2,517 units and 235,356 rows. Its brand-new import
passed all 141 independent typed-table hashes and full schema parity in
59.72 seconds. The frozen file closure contains 14,462 files, 248,165,846 bytes,
nine input bundles and verified witnesses for all 61 historical code scopes.
Hydration uses the unchanged 2,723-generation source witness.

The final graph/re-key passed all 6,498 units, with none excluded or noncurrent,
in 232.37 seconds. Ordinary materialization committed 1,468 required units and
passed 2,936 exact approved-source comparisons in 176.03 seconds.
Local A publication, including its mandatory backup and separate owner audit,
passed in 455.14 seconds. B's required backup and owner check passed in
430.96 seconds.
Normal next-day candidate B remained genuinely pending with local head A after
a deliberate precommit failure. Its actual mandatory prepublication backup
restored into a fresh database and empty volume in 219.68 seconds. Normal
head-A reconciliation, hydration and replay completed in 174.17 seconds.
Fresh C matched all 21 of B's file hashes and content hashes, schemas, keys,
row counts, declared versions, bundle labels, rendering inputs and parent A.
The complete post-build audit passed all 141 tables, including the documented
monotonic storage-clock and candidate-pointer checks.
The only policy difference was the documented bookkeeping
`release_policy.revisions` map. Candidate id and build fingerprint identify
the fresh attempt; the receipt lists their literal values and every counter
delta. Render inputs, content hashes and released bytes were not normalized.
The real release contains 1,402 dancers and 9,437 registry placements. All
18 Parquet schemas, row counts and unique primary keys passed. The original
113 pipeline inputs, including the absence token, all 1,468 selected project
receipts and the full original B build receipt remained exact. The object audit
checked 4,627 clocks, accepting exactly one normal advance.

B's checked backup transported 336,488,928 bytes. The
[final sample receipt](../../evidence/runtime/postgres-stage1-2026-11-04/sample-final.json)
records its actual set, immutable source and report hashes, all 21 file hashes,
release counts and the literal permitted metadata differences.

The sample is a bounded registry cohort. Its release has no events, contests,
rounds or entries.
It does not replace the full-size server rehearsal in stage 2. The full suite
covers event and result paths with offline fixtures and retained golden hashes.

## Regression and developer feedback

The maintained [developer gate](../../evidence/runtime/postgres-stage1-2026-11-04/developer-current.json)
passed all 2,583 core cases in 179.39 seconds internally and 179.50 for the
whole command. Cached readiness took 0.61 seconds. Slowest warm startup,
admission, projection and replay samples were 0.53, 0.82, 0.45 and 2.34 seconds.
Every sample counted, with heavy jobs idle. The AMD64 runtime built in 6.47
seconds and passed UID 10001, read-only, network-disabled and separate
worker/owner IPC checks. Global formatting, Ruff and mypy's 338 sources passed.
The [native four-worker full suite](../../evidence/runtime/postgres-stage1-2026-11-04/full-native-final.json)
passed all 4,147 cases with no failures,
errors or skips in 1,363.70 seconds, including supervision. Collection,
executed membership and source binding matched exactly. Across the four
workers, collection totalled 1.62 seconds, setup 803.66, bodies 4,523.17 and
teardown 40.06; these overlapping totals are not elapsed wall time. The application source
and all 879 test/runtime inputs stayed frozen across the final proofs.

One repeated core run finished within budget but lost a worker during a nested
SQL deadline test. No original worker stack was emitted. A deterministic test
then exposed an unprotected timer-arming window: immediate expiry escaped as
`KeyboardInterrupt` and left the handler and deadline installed. Arming now
runs inside the existing protected boundary. The regression checks rollback,
restored timer and handler, firing count and connection reuse. This proves the
boundary defect; it does not establish the missing historical traceback. The
timing hook also accepts xdist's synthetic crash-report phase, preserving its
failed result and original crash instead of raising a second exception.

The 30 ms recovery test also timed out its healthy follow-up under unrelated
parallel load. An idle trace showed fresh bounds and no leaked timer, with the
healthy derivation completing in 21.40 ms. The default core now runs that
unchanged test in one isolated phase before the four parallel clients. All
phases report the complete core selection; their assigned and executed cases
must form its exact disjoint union. A failed prologue stops the run without
retry. The 180-second budget includes both phases, startup and cleanup.

Ordinary core physical checks reuse a separate durable service. Every use
checks its exact identity, settings, roles and database inventory against the
known-fresh baseline. PID1 reaps its own Copies on both endpoints; the host
requires the baseline inventory before promoting a result or deleting passed
volumes. Unexpected state retains its owner records and blocks reuse. Fault
tests, full runs and CI keep fresh clusters. Process polling returns promptly
after completion while retaining the live resource-sampling cadence.

No timeout, memory limit, selected test or durability assertion was reduced.
The first complete extended run executed 4,076 cases, with 4,026 passes and
50 failures; its [receipt](../../evidence/runtime/postgres-stage1-2026-11-04/full-native-first.json)
remains. Fixes cover native ownership and locking fixtures, isolated nested
pytest configuration, retained historical SQLite tests, and bounded changelog
spooling. The original 32 MB DuckDB and 8 MiB Python-memory assertions remain.

The 50,000-file backup case initially exceeded its 300 MiB writer budget.
Capture now releases exhausted path and manifest inventories before independent
verification. Restore releases extraction indexes only after every indexed file
and digest passes. The original case uses the production's separate owner
checker and independently restores the history and every file; owner credentials
are absent from the worker. The final writer peaked at 272.02 MiB, below its unchanged 300 MiB limit,
before and after the complete, equal owner check. It captured all 50,000 files
and four database parts (222,873,600 raw database bytes).

One concurrent feedback attempt took 14.235 seconds for admission. Its first
sample was incorrectly relabeled cold in the
[intermediate receipt](../../evidence/runtime/postgres-stage1-2026-11-04/feedback-final-current.json).
That label is superseded. An earlier declared protocol created the absent
physical service in 3.7518 seconds with TEST, DEV, dependencies and seed cached.
That historical proof remains source-qualified. The current source's initial
readiness takes 0.6065 seconds with all three services and seed cached, followed
by exactly three samples per group with heavy jobs idle; every sample counts.
Core scheduling uses measured whole-file
costs only to order work. Exact selection, assignments, executions, source
binding, resource caps and cleanup are checked before costs can be refreshed.

Raw reports and retained sample and failed states remain in scratch. Successful
full-suite temporary directories were removed after an independent owner,
identity, open-file and preserved-report check. Compact receipts record
the commands, source hashes, counts, timing and exits. Reproduce with
`mise run dev`, `mise run test -- <selector>` and
`mise run test -- --full-suite`.
