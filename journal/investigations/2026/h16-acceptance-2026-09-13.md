# Checking the evidence needed for a release (H16)

> Historical record. Dates, status claims, and instructions below describe the
> original work. See [current status](../../../docs/status.md) before acting.
> [Project journal](../../README.md)

A release needs saved evidence for every selected result. This record concerns checking that evidence, completing a build, or preparing deployment. The original work ID is H16.

H16 is deployed and its read-only production acceptance passed. Full retained
initialization and release rehearsal remain in progress. H16 has not published
a production release or activated repairs.

## Release behavior

Normal releases select a cutoff and exact compatible immutable generations.
New unrelated source evidence cannot invalidate that selection. Current
corrections, source revocations, contracts, suppressions, and the acknowledged
baseline still fence completion and publication. An incompatible link is
withheld; a partial column update cannot create a missing structural row.

The full proof stays in private immutable storage. Public manifests contain
opaque generation IDs and proof commitments. Private input retention precedes
candidate files; a separate durable build receipt is required for publication.
A crash between those steps leaves recoverable files and no completed build.

Coverage distinguishes source units, omitted derivation scopes, and identity
subjects. Unknown discovery denominators remain unknown. Source, year, and
event scopes expose evidence and verification times; common facts expose
scope status. Historical suppression finishes before final coverage counts
and their changelog deltas are written. Named judges without WSDC numbers
remain valid records with null IDs.

Unchanged evidence reuses its candidate within the daily health cadence.
Reusing a published baseline does not make another remote commit. Doctor
separates local candidate readiness from the acknowledged release and keeps
the oldest recorded pending correction age. Missing older clocks remain
explicitly unknown or a known lower bound.

## Local evidence

Independent scenarios cover six harmless source arrivals during and after
build, incompatible event/dancer/alias generations, revoked or omitted support,
current correction and contract changes, durable blocked parse work, baseline
races, missing build completion, artifact corruption, private-proof tampering,
legacy public float representation, and receipt-only publication progress.

The main suite passed 1,088 tests in 83.86 seconds. Six bootstrap checks
and both crash/restart checks passed; the crash sweep took 165.13 seconds.
Ruff passed and mypy checked all 163 runtime modules. Additional operational
driver and initialization checks are recorded separately as they finish.
All 1,120 collected tests are covered by successful main, crash/bootstrap,
and additional operational/replay runs. The operational receipts below
passed after deployment. The [operational driver preparation](../undated/h16-deployment-preparation.md)
describes its exact read-only acceptance boundary.

## Retained scale rehearsal

The [offline selection profile](h15-offline-selection-profile-2026-09-13.md)
records the isolated production-copy measurements. Repeated inventory scans
were removed without changing selected work. A frozen H16 rehearsal completed
100 actual project scopes in 54.566 seconds: 43.926 seconds selecting work and
10.458 seconds deriving it. Every scope succeeded; SQLite grew about 1.26 MiB.
It made no source requests, parsed no bodies, and built or published no release.

This base-scope sample does not prove full-graph convergence, release closure
reconstruction time, or production memory use. A bounded initialization through
actual workers and a complete retained release rehearsal remain outstanding.

## Predecessor and publication

The verified schema14 predecessor checkpoint is
`/var/lib/swingset/checkpoints/h15-before-h16-20260913`, private commit
`c656e88c775b23ae5879661924d57fa1f93cfa7d`, manifest SHA-256
`700b2adc01db9eefc5c0c7ba35fddabfbc3aa00b6ed6f4900543b4e4a54ae444`.
The public baseline remains V4 commit
`81121dcc7d62a1db9e76ee1c49a6b90e1f4c5653`. Scheduled workers remain held.

## First deployed pin and operational acceptance

- Source: `/nix/store/d8bwqgbqi94b19l0wfgqr7a2gir1xkf5-source`.
- Source receipt SHA-256: `abb69e5f559769bd2b6ae782c290c10ff746df3783f4118a2b107e440c462983`.
- NixOS system: `/nix/store/jvp496x98i20fqcmpz0ikam96lyihbcs-nixos-system-swingset-lxc-25.11.20260630.b6018f8`.
- Activation: `2026-09-13T12:41:15.789323+00:00`.
- Frozen source: 587 files plus its source receipt.
- Operations driver SHA-256: `8ed4b2384fb946100934a14b57025b6cc6272fb7927632eaede5c19eb2519f50`.
- Gate SHA-256: `eadf610b11a665c76d921b4f37df04a8ffba5cebbf09bafcdc6604a416015703`.

The [execution receipt](../../evidence/releases/h16/h16-production-execution.json) passed in
42.218 seconds after preflight. It preserved every user table, column, schema
object, and autoincrement sequence. Schema remained 14. It ran no migration,
source request, service worker, or publication. The public baseline and all
six inactive worker/timer units were preserved.

Doctor matched in fresh processes at `2026-09-13T12:42:46.558385+00:00`;
its report SHA-256 is
`fda56b663287eb47cc3c1cf12ae8d6a1b5293603c6f09f964fbb7efab63f4752`.
Human and JSON requirement inventories agreed. Full reports remain private
operations artifacts: 177,113,696 JSON bytes and 150,589,225 human-readable bytes.

The live inventory still has 34,986 unfinished project/link scopes and zero
materialized pointers. This receipt proves deployment compatibility, not local
convergence or published progress. The full initialization uses a separate
marker-bound SQLite copy and real project/link workers; no scratch work is
represented as completed production work.

## Initialization performance follow-up

Two bounded invocations on the isolated copy committed 29,638 generations with
zero failed workers. Each stopped normally near its 550-second start-next-unit
boundary. The first completed 29,048 base scopes; the second completed 590
remaining base, shared, and event scopes. The copy is paused with unfinished
work, and its receipts remain under `verification/h16-full-replay-*`.

Event work exposed a repeated full catalog scan for the map-only prerequisite.
A narrow indexed lookup preserves registered, queued, physical, and inferred
map scopes, including custom scope IDs. It changes no write-transaction cache
rule. On the same paused state, the profiled ready/current/desired sequence
took 0.686 seconds with deployed code and 0.005 seconds with the experimental
lookup. Both selected the same dependency fingerprint. These are single paired
profiles, not worker throughput or convergence measurements. The
[measurement receipt](../../evidence/releases/h16/h16-map-lookup-profile-20260913.json)
binds the experimental module and captured logs.

The optimization passed 55 focused tests, including 31 catalog/invalidation
checks, and independent review. Exact link subset queries reduced profiled
desired selection from 1.302 to 0.263 seconds. Batching the same immutable
dancer currentness proofs reduced profiled readiness from 2.829 to 0.633 seconds.
All 29,048 registry dependencies, fingerprints, and dependency-set hashes remain
identical. Missing or changed proof uses the existing full check; no answer
survives the call or a rollback. Mutation, missing-proof, rollback,
caller-transaction, and concurrent-read tests pass. The resulting deployment is
recorded below.

The retained V4 coverage schema also exposed a changelog compatibility defect:
its rows have year/source/via keys and no scope columns. Comparing them with
the new scope key collapsed different years under null scope values. The
follow-up maps each old year key to its new year-scope identity for comparison,
retains the old payload, and records added scope fields as ordinary updates.
Two old-format Parquet regressions failed before the fix; all 40 combined
builder, coverage, and migration checks pass afterward. Historical changelog
files are not rewritten.

## Performance follow-up deployed

The follow-up deployed at `2026-09-13T13:31:43.107786+00:00`:

- Active source: `/nix/store/dihx01r68nq8nqxzm1dlgrzl5qrjw583-source`.
- Source receipt: `92cb3a524a5f11b70df37db2a988c312a0256d0f7a55e65d34ed1686aaff059b`.
- System: `/nix/store/r29kapzbjk0ch5h0vkk288jd47dvg90j-nixos-system-swingset-lxc-25.11.20260630.b6018f8`.
- Gate: `96c0a2d25f8d5f1ca3746364948e116a2e0bbfca00c9fc9fa06fc15a170876d5`.
- Source files: 602 plus its receipt; schema remains 14.

All 1,186 tests in the isolated source mirror passed in 247.70 seconds,
including crash/restart tests. The mirror supplied two unchanged retained CDX
test inputs; child processes used its explicit module path. An earlier test
invocation exposed those two setup omissions before the clean run. Ruff passed
and mypy checked all 164 runtime modules. Independent review found no blocker
in subset selection, bulk proof checking, or coverage key compatibility.

The [production execution receipt](../../evidence/releases/h16/h16-performance-production-execution.json)
passed in 44.980 seconds after preflight. Every protected table, column, schema
object and autoincrement sequence remained unchanged. Doctor matched in fresh
processes at `2026-09-13T13:33:25.035802+00:00`, report SHA-256
`668d2204812617ffe15b2bfd2b338fc7d29bf224efcd5cd25026af1517bc6e31`.
Human and JSON requirement inventories agreed. All six ordinary units stayed
held; no migration, source request, service worker, or publication ran.

The optimized replay starts from a new independent checkpoint copy at
`/var/tmp/swingset-h16-optimized-replay`. Its accepted bundle is
`c7ffb3b3e1dd2e210e9884d7fb654c5c5291d08a186291d77fcfa97522ae2c49`.
The earlier 29,638-generation experiment remains intact. Full convergence,
the separate baseline-backed build rehearsal, substantive candidate audit,
production initialization, and publication are still outstanding.

## Retained replay defects under investigation

The optimized first invocation committed 28,819 units without failures. The
second committed another 3,381 units, bringing the retained total to 32,200,
before the coordinator stopped it between workers. Five event projections
failed foreign-key constraints; their transactions rolled back and the retained
database passes `foreign_key_check`. Sixteen source-event attempts also reported
`inputs_changed`. Both outcomes require diagnosis before a new replay or build.
The [second receipt](../../evidence/releases/h16/h16-optimized-replay-002-20260913.json)
records the interruption; it is not a convergence receipt. The disposable state
and failure evidence remain intact. Production and the public V4 baseline are
unchanged.

The retained failures share an ownership defect. Map reconciliation recognized
five source-created events as registry events, kept references to them, then
removed their last canonical owner. Subsequent contest insertion failed. An
unmapped source-event worker also reran that map and encountered a different
output under identical inputs. The fix retains ownership for still-mapped
events and transfers an enriched event from a removed override to its surviving
source mapping. Unreferenced superseded events still retire.

Focused regressions cover repeated identical map output, a genuinely unmapped
undated source event, removal of obsolete targets, and override ownership
transfer. A [checkpoint-based reproduction](../../evidence/releases/h16/h16-map-retention-reproduction-20260913.json)
passed all five former event failures, with clean foreign keys and unchanged
output on the second map pass. This validates the fix locally; a new frozen
source, complete replay and release acceptance remain required.

The corrected schema14 source is now frozen at
`/nix/store/kshnpz73b37h89zkmj03f4b3srd5ldaf-source`, receipt SHA-256
`1b42aef638b49b3579d1ab02a67c4135db0519b1dd57d55f4f0d3aee3a21f4cb`.
All 1,191 isolated tests passed in 275.04 seconds, including crash/restart;
Ruff passed and mypy checked 164 runtime modules. The
[validation receipt](../../evidence/releases/h16/h16-event-map-source-validation-20260913.json)
records the independent mirror and its setup correction. The corresponding
NixOS system is built but not deployed. A new independent replay at
`/var/tmp/swingset-h16-event-map-replay` uses accepted bundle
`e5e8df491d9e31ff008ed873d1254fe1438f0e41ddae1ec9a96cc7bb912de42a`.
Both prior replay states remain intact.

The production initializer has also passed 33 isolated guard checks under this
frozen runtime. Its reviewed receipt chain binds the exact replay, build and
audited candidate bytes. Accepted input authority and the identity decision
revision are preserved. A reviewed continuation marker permits a completed
pause/resume without changing controls or reaccepting inputs. The
[driver receipt](../../evidence/releases/h16/h16-operational-driver-review-20260913.json)
retains the exact scripts and tests. These scripts have not run in production.

The separate production build/publication driver passed 34 isolated guard
checks under the same frozen runtime. Its publication gate requires the actual
production initialization, build and substantive audit; scratch success alone
cannot authorize a candidate. Lost-response recovery verifies the existing
publication before resubmitting. The
[review receipt](../../evidence/releases/h16/h16-production-release-driver-review-20260913.json)
retains the driver and its tests. Actual build and publication remain pending.

The corrected full replay converged at invocation 011: 32,445 project scopes
and 2,541 event links are current, with no unfinished work or failed attempts.
All 21 formerly failing cases succeeded. Eleven invocations took 5,755.405
seconds in total; foreign keys are clean and the writer lock is released. The
[final invocation](../../evidence/releases/h16/h16-event-map-replay-011-20260913.json) and
[aggregate accounting](../../evidence/releases/h16/h16-event-map-replay-summary-20260913.json)
retain the evidence. The separate baseline-backed build rehearsal has started;
production initialization and publication are not yet complete.

The separate build was stopped by its external memory monitor at 6,374,916
KiB anonymous memory, before the first table file was written. It produced no
passing build receipt. The [stop assessment](../../evidence/releases/h16/h16-event-map-build-stop-20260913.json)
retains memory samples and the unit journal. The generated changelog still
materializes its entire new delta in memory, even though old history is
streamed. That pre-write path is being bounded; no captured stack establishes
the precise line executing at termination. The completed replay and stopped
build state remain intact, and production is unchanged.

The bounded changelog fix is frozen at
`/nix/store/093lylp4naslq5yb2mygbkkgkbsar3ym-source`, source receipt SHA-256
`3930dbc9f6023250a5253043ade60bd3fc90c57bda27e2c64f8520aa4c588a92`.
The [isolated schema14 run](../../evidence/releases/h16/h16-changelog-frozen-validation-20260913.json)
passed 1,199 tests in 320.26 seconds, Ruff, and mypy for 165 modules. The
[worktree run](../../evidence/releases/h16/h16-changelog-worktree-validation-20260913.json)
passed 1,396 tests in 322.62 seconds. Independent review verified UTC
normalization, ordering, suppression passes, legacy coverage keys, and cleanup.
The NixOS system is built but not deployed. A new checkpoint-based replay uses
bundle `9370ccc35058661037c78b2dbcea797932dce1e4211638826149661a4e37e683`.

The new pin's [production preflight](../../evidence/releases/h16/h16-changelog-production-preflight.json)
passed using retained private-backup verification and current local baseline
hashes. It made no requests and performed no migration, worker execution, or
publication. Two launch setup failures preceded that successful preflight:
the service account has no login shell, then a mistyped import path prevented
module loading. Their [journal](../../evidence/releases/h16/h16-changelog-preflight-setup-20260913.log)
is retained. Successful preflight is not deployment acceptance or a completed
release rehearsal; those still depend on the new replay and build.

The owner subsequently authorized completion of only the paused scratch replay.
Invocations 007–012 completed the remaining 1,418 scopes in 50.57 minutes of
invocation time. The [final receipt](../../evidence/releases/h16/h16-changelog-replay-012-20260913.json)
reports `current`: all 32,445 project scopes and 2,541 link scopes are complete,
with zero failed attempts and no unfinished project/link work.
The [complete receipt chain](../../evidence/releases/h16/h16-changelog-replay-summary-20260913.json)
accounts for all 34,986 successful scopes across 12 invocations.

[Independent read-only verification](../../evidence/releases/h16/h16-changelog-replay-final-verification-20260913.json)
confirmed authoritative currentness, selected generation counts, clean foreign
keys, completed replay runs and the released writer lock. Seven historical run
records without finish timestamps match the predecessor checkpoint and remain
unchanged. The first verification assertion incorrectly required every inherited
run to be closed; the corrected check requires exact predecessor preservation
and completion of all actual replay runs. No replay data repair was needed.

Work stopped at the owner's requested boundary. No new build state, deployment,
production initialization, publication or source request occurred. Production
remains schema14 with zero materialized pointers, the same public baseline and
all six ordinary service/timer units inactive. The new pin's full build and
release acceptance remain outstanding.
