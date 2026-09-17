# Offline selector performance investigation, 2026-09-17

The first ordinary scratch drain preserved its inputs and controls but committed
only four units before the selection budget ended. This investigation measures
selection separately from workers before proposing a runtime change.

## Retained observation

The [drain receipt](../../evidence/runtime/extension-input-rehearsal-2026-09-17/production-copy-001/drain-001.json)
records a turn of 550.008329928 seconds, one successful parse and three successful
projections. Pending queue counts were 31,820 parses and two projections; these
counts do not establish the complete derived work population. All 4,931 initially
named judges remained intact. No acquisition, repair activation or publication
occurred. The receipt explicitly sets production acceptance to false.

The phase began at 17:05:40 UTC and finished at 17:15:26 UTC. Its measured worker
turn stopped at the selection boundary with zero reported budget overrun.
Complete retained-file and other preservation checks occur outside that turn.
The later process exit is not evidence that selection overran its deadline.
The coordinator's separate attempt query reported the final three projections
completed between 17:05:51 and 17:05:55 UTC, followed by no newer attempts while
the selector continued. The profile below tests that explanation.

## Exact source paths and hypotheses

Source 003 is `/nix/store/dx0cyzvd8d91rf29v21sakbr7l5bwxnz-source`, receipt
`60cdfe64004a0ba5a9aee207bcb089d6d4c81f5ca58169ccdb88a5dfab8146d6`.
Inspection used its receipt-verified local mirror, not later working-copy code.

- `schedule/fairness.py:453` wraps `next_offline` in a query-local cache, then
  discovers every known project/link scope to obtain stage/kind groups. Groups
  rotate by their last service sequence.
- `state/work.py:85` exhausts a group's candidate iterator until it finds an
  allowed, ready and eligible unit. There is no candidate-count limit before
  moving to another group.
- `state/derivations.py:474` checks queue/catalog hints first, then falls back to
  every known scope. Currentness may fall back from a cheap certificate to a
  full desired dependency manifest. This fallback is required for correctness;
  the queue cannot become the complete work authority.
- `state/derivations.py:511` opens a plain `BEGIN` for link readiness, with
  `query_only` still false. `state/derivation_query.py:40` correctly disables
  memoization inside such a potentially mutable transaction. Each link candidate
  can therefore reload its full dancer prerequisite population.
- `state/derivation_readiness.py:16` loads all dancer pointers, raw versions and
  watch associations before checking whether that cohort is current. A stale
  dancer may block the link only after those full reads.

This can multiply population-wide readiness work by the number of blocked link
candidates. It is not a link candidate-pool rebuild: `link_event` runs only after
selection and worker admission. Repeated event currentness or global projection
readiness is another possible cost. These remain source-derived hypotheses until
the retained profile identifies where time is spent.

The [work contract](../../../docs/reference/recovery/work.md) requires independent
progress and fair allocation. The [derivation query contract](../../../docs/reference/recovery/requirements.md#derivation-work-is-a-query)
requires discovery of stale retained scopes even if queue hints are missing.
A fix must satisfy both. Candidate seams include a proved read-only selection
snapshot and query-local shared readiness, or bounded group traversal with a
safe continuation. Removing the mutable-transaction cache guard is not safe:
writes and savepoint rollback must invalidate every prior answer.

## Profiling helper

[profile_offline_selector.py](../../tools/runtime/profile_offline_selector.py)
profiles exactly one ordinary `next_offline` call. It verifies source 003, the
hashed scratch marker, schema 28 and the retained hold. It rejects production and
checkpoint paths, takes the existing scratch writer lock, opens `mode=ro`, and
adds a SQL authorizer denying writes and attachment. It preserves the ordinary
`query_only=0` baseline to reproduce readiness behavior. A Python alarm and SQL
progress callback bound the selection; both are cleared on exit. No worker runs.

The report includes cProfile results, current/desired/ready counters by kind,
dancer-readiness transaction modes, total database changes of zero and an
unchanged data-version check. Profiling overhead is explicit. Source/interlock
checks are outside the measured selection interval. No SQL trace callback is
installed, avoiding callback exception handling that could swallow an alarm.

Four offline checks passed in 0.09 seconds, plus Ruff and mypy. They verify Python
and SQL timeouts, cleared timers/restored instrumentation, denied mutation and
attachment, and a successful selection report. The coordinator independently
repeated the four tests before staging the helper. The helper SHA is
`b637c20b0002f9c2e4452ebede0363b9ccfb104983ab065f5d2ba59533f81ca8`.

Coordinator invocation:

```text
python profile_offline_selector.py
  --source /nix/store/dx0cyzvd8d91rf29v21sakbr7l5bwxnz-source
  --scratch <same quiet disposable scratch>
  --marker-sha256 <retained marker SHA>
  --helper-sha256 b637c20b0002f9c2e4452ebede0363b9ccfb104983ab065f5d2ba59533f81ca8
  --output <new directory outside scratch, source and production>
  --seconds 30
```

Use `PYTHONDONTWRITEBYTECODE=1` and an external service lifetime bound for startup
and report retention. The coordinator launched the first specimen after the
scratch drain closed. That initial profiling helper makes no runtime change;
the measured result and subsequent local fix are recorded below.

See [D-0072](../../decisions/0072-profile-offline-selection-before-changing-replay.md).

## Measured selector cost and local fix

The [baseline profile](../../evidence/runtime/offline-selector-profile-2026-09-17/baseline-001/report.json)
stopped after 30.0095 seconds. It recorded 978 map-currentness checks and 978 map
`desired` calculations, costing 19.81 cumulative seconds in `desired`. There
were 977 event-readiness checks, all false. Each rebuilt the same map's continuity
support from retained events and source mappings. Two inventory `desired`
calculations added 5.48 cumulative seconds. These times overlap callers and must
not be added as independent totals. The profile had not reached dancer readiness;
the earlier link hypothesis remains unconfirmed by this sample.

A local three-file fix now wraps ordinary `next_offline` selection in an owned
read-only transaction and reuses exact currentness answers in a 2,048-entry LRU
for that snapshot only. The key includes the complete work unit and serialized
context. A repeated false map result therefore avoids rebuilding identical
support for every blocked event. Selection still visits the same groups and
candidates, applies the same eligibility rules, and keeps complete fallback
scope discovery. No queue becomes authoritative and no page is marked complete.

Caller-owned transactions are left unchanged and receive no new currentness
cache. Worker transactions, inline derivation groups and build/filesystem checks
keep their ordinary behavior. The selection restores its transaction and
`query_only` state on success or failure. Writes from a selection callback are
rejected. A result is only a hint: admission rechecks controls and dependencies
after the snapshot closes. The cache is dropped at that boundary, and a changed
connection data version clears it. Same-connection mutation disables reuse.

Sixty-nine focused tests passed in 15.71 seconds, covering derivation
certificates, fairness, H15 acceptance, replay and manual controls. After a
test-only lint correction, the ten new cache tests passed again in 1.84 seconds;
Ruff and mypy over the three changed source modules passed. A real fixture with
40 blocked event candidates computes shared map desire once and selects the
same healthy parse as an uncached reference. Other tests cover LRU eviction,
failed computations, distinct contexts, same/other-connection changes, mutable
transactions and savepoint rollback, callback write rejection, exception
cleanup, fresh admission after a concurrent pause, and uncached build checks.

The runtime files are locally implemented and tested, not deployed by this
change. The coordinator is creating a separate schema-28 comparison source from
immutable source 003 plus these exact three files, excluding unrelated local
schema-29 and parser changes. A new source-bound profile is still required to
measure the improvement on the same retained scratch.

## Comparison helper review

The coordinator froze a separate schema-28 derivative containing only the three
reviewed runtime changes. Its receipt is
`acfe304af71280843a2087b4e415118d81aaa8800a58e48620fbfe3ca018df41`,
with source 003's receipt as its explicit predecessor. The retained
[source selection](../../evidence/runtime/offline-selector-profile-2026-09-17/comparison-source-001/selection.json)
records all three hashes. This is a profiling candidate, not a deployment.

The separate comparison profiler passed independent review:
[receipt](../../evidence/runtime/offline-selector-profile-2026-09-17/comparison-independent-review-001/checks.json).
The four existing profiler tests passed against this new helper in 0.08 seconds.
The reviewer verified the complete derived source inventory and predecessor,
rejected a changed scratch parent marker before any database access or output,
and checked that permitting `query_only` toggles still denies writes. SQLite
`mode=ro`, writer serialization, no-network guard and separate Python/SQLite
bounds remain intact. No profiler operation ran during that review.

## Measured cohort scan and second local fix

[Comparison 001](../../evidence/runtime/offline-selector-profile-2026-09-17/comparison-001/report.json)
still timed out at 30.0075 seconds. Map currentness ran 3,009 times but needed
only one desired calculation, about 0.020 seconds. That confirms the first
fix's intended effect within this instrumented sample. The next observed cost
was `dancers_current`: 168 calls used 21.61 cumulative seconds, including 20.98
seconds in the function itself. Link event readiness used 22.22 cumulative
seconds. These overlapping times must not be added. The profile does not prove
uninstrumented production throughput or a complete queue scan.

[D-0075](../../decisions/0075-reuse-dancer-readiness-only-within-owned-read-snapshots.md)
records a second narrow local change in `derivation_readiness.py`. The complete
ordered dancer cohort now shares its boolean proof result in the existing
2,048-entry cache inside an owned selection snapshot. The ordinary currentness
callback is part of the key; custom callbacks always recompute because their
state need not be in SQLite. Caller-owned transactions receive no new cache.
Workers, consistency groups, candidate ordering and admission are unchanged.

The first focused check passed **35 tests in 6.75 seconds** across the new cohort
suite, existing bulk-readiness suite and first currentness-cache suite. Ruff and
mypy passed for the changed source module. A real 40-event differential fixture
reads the dancer cohort once and selects the same healthy parse as the uncached
reference, which repeats the scan at least 40 times. Other new checks cover
changed cohorts, accepted input changes, mutable transactions and savepoint
rollback, concurrent writes across snapshot boundaries, caller-owned read-only
transactions, unhashable custom callbacks with mutable state, failed proofs and
LRU eviction. The second runtime change is locally tested, not deployed. A
separately frozen profile must measure its effect on the retained scratch.

A further 46 fairness, H15 acceptance, manual-control and derivation-certificate
tests passed in 10.69 seconds. The
[source-bound offline receipt](../../evidence/runtime/offline-selector-profile-2026-09-17/cohort-cache-offline-check-001/checks.json)
records both focused runs and the frozen source/test hashes. These are separate
runs, not a full-suite claim.

## Early shared rejection before link candidate currentness

[Comparison 003](../../evidence/runtime/offline-selector-profile-2026-09-17/comparison-003/report.json)
retained another timeout at 30.0131 seconds. Shared dancer readiness now needed
about 1.85 cumulative seconds across 2,153 calls, but link currentness consumed
16.27 cumulative seconds. Dependency expansion produced over 62 million
generator items despite the shared prerequisite blocking every evaluated link.
The coordinator reported a backup running concurrently; the wall time is not
an isolated throughput estimate, and cumulative caller times overlap.

[D-0085](../../decisions/0085-check-shared-link-readiness-before-candidate-scans.md)
adds one narrow check in `fairness.py`: within a newly owned read snapshot,
reject a link group whose ordinary shared dancer prerequisite is false before
asking `next_work` to enumerate its candidate currentness. A true answer still
requires all original per-event checks. Group order, complete known-scope
fallback, retry eligibility, exclusions and controls remain unchanged.
Caller-owned transactions, including consistency groups, bypass this early
optimization and preserve their existing path. No worker semantics change.

Forty focused tests passed in 5.82 seconds, with Ruff and mypy over the changed
source module. The [source-bound receipt](../../evidence/runtime/offline-selector-profile-2026-09-17/link-group-offline-check-001/checks.json)
records the exact source and test hashes. New differential tests prove the same
healthy parse is selected while a blocked link fleet avoids candidate
currentness; true shared proofs still check event readiness. Further tests
preserve physical dancer and event scopes after both queue and link-catalog
hints are removed, observe reopening after a new snapshot, keep mutable caller
transactions and consistency groups intact, and honor exclusions and pauses.
The runtime change is locally implemented and tested, not deployed. A new
source-bound profile remains necessary to measure the retained scratch result.
