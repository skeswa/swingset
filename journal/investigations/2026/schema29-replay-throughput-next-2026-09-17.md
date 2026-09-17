# Schema 29 replay timing: next diagnostic

The two completed scratch drains each spent about seven minutes for 100 attempts,
but their receipts do not separate selector time from work execution. Before
optimizing parsing or projection, query the persisted attempt intervals from
those exact run IDs on the closed disposable scratch. This provides stage
execution time and the time between attempts without rerunning work. The gaps
are a selection-cost proxy, not a direct measurement of `next_offline`.

## Retained replay evidence

Both receipts bind candidate 005 at source
`/nix/store/z0rnsn69aav2h8p2wgzydar6k9rw5kk9-source`, source receipt
`9255e8641a24c21c0942512ec251b32dd294bc6f2a537868a0012cafe331dc99`, and input
bundle `f4e789c30a69d8f439f2e1068f7a620d6cff8160d329e05474ab5f92929b5426`.
They use schema-29 scratch
`/var/tmp/swingset-schema29-input-20260917-001` and the same checkpoint-002
manifest `4929859092cbd3e15122f3598b5da477a65498976cc744c52cf2b142d1cebea3`.

| Run                    |  Elapsed | Attempts | Successful parses | Successful projections | Other outcomes                       | Pending parse afterward |
| ---------------------- | -------: | -------: | ----------------: | ---------------------: | ------------------------------------ | ----------------------: |
| `run_20260917T185409Z` | 402.17 s |      100 |                32 |                     67 | 1 admission review                   |                  31,789 |
| `run_20260917T191421Z` | 413.86 s |      100 |                31 |                     67 | 1 admission review, 1 unit exception |                  31,758 |

The first and second receipts are `inputs-001/drain-001.json` and
`inputs-001/drain-002.json`. Neither exceeded its selection budget or recorded
an interrupted attempt. The second exception was a `KeyError` for the manual
page kind `registry_crosscheck`; it is a routing defect from candidate 005, not
successful work. These are bounded turns, not sustained service measurements.

The logs report 6.4 GiB peak memory and 16.8/17.5 GiB read from disk per turn.
Those are whole-service totals, not per-attempt measurements, and do not identify
whether selection, parsing, projection, SQLite, or cache pressure caused the
cost.

## What existing timing does and does not show

The prior selector comparison 004 profiled one `next_offline` call in 9.095 s
and selected a parse unit. Its report attributes 6.575 s cumulatively to
derivation currentness checks, about 3.95 s to SQLite `execute`, and substantial
work to enumerating and hashing derivation dependencies. Inventory current and
desired checks alone each took about 3.5 s. Earlier comparisons 002 and 003
reached their 30 s limits without finding a unit.

This is evidence that selection can be costly. It does not apportion either
400-second replay: comparison 004 is a separately instrumented single selection
on the earlier schema-28 profiling scratch, with cProfile and current/desired/
ready wrappers. Its own report says the result is instrumented, has no SQL trace,
and is not ordinary throughput. Do not multiply its 9.095 s by 100 or treat it
as a measurement of the schema-29 drains.

The scheduler calls `next_offline`, then `derive_one`, then records offline
service before selecting again (`schedule/cycle.py`). `derive_one` captures a
project/link derivation before it commits a `work_attempts.started_at` token;
then parsing or projection runs, and `finished_at` is assigned before the
surrounding output transaction exits. Thus attempt intervals include parser or
projector work, fencing, and database statements, but exclude some pre-attempt
capture and the final outer transaction exit. A gap from one attempt's finish to
the next start includes scheduler selection, service-accounting writes, and the
next attempt's admission/capture. It is not a pure selector timer.

## Read-only timing query

On the completed scratch only, run the following after confirming no replay
service is active. Open `state.sqlite` read-only with the project's normal
read-only/WAL-aware connection behavior; set `query_only` and keep the query in
one read transaction. Do not query the production database or checkpoint. The
query reads the indexed `work_attempts_run` rows and returns execution and
between-attempt seconds by run and by the stage of the next attempt.

```sql
PRAGMA query_only = ON;
BEGIN;

WITH ordered AS (
    SELECT
        run_id,
        attempt_id,
        stage,
        unit_kind,
        outcome,
        started_at,
        finished_at,
        (julianday(finished_at) - julianday(started_at)) * 86400.0
            AS execution_seconds,
        LAG(finished_at) OVER (
            PARTITION BY run_id ORDER BY started_at, attempt_id
        ) AS previous_finished_at
    FROM work_attempts
    WHERE run_id IN ('run_20260917T185409Z', 'run_20260917T191421Z')
),
timed AS (
    SELECT
        *,
        CASE
            WHEN previous_finished_at IS NULL THEN NULL
            ELSE (julianday(started_at) - julianday(previous_finished_at)) * 86400.0
        END AS between_attempt_seconds
    FROM ordered
)
SELECT
    run_id,
    stage,
    COUNT(*) AS attempts,
    SUM(finished_at IS NULL OR outcome = 'running') AS unfinished,
    SUM(outcome = 'succeeded') AS succeeded,
    SUM(outcome <> 'succeeded' AND outcome <> 'running') AS non_successful,
    ROUND(SUM(execution_seconds), 3) AS execution_seconds,
    ROUND(AVG(execution_seconds), 3) AS mean_execution_seconds,
    ROUND(MAX(execution_seconds), 3) AS max_execution_seconds,
    ROUND(SUM(between_attempt_seconds), 3) AS preceding_gap_seconds,
    ROUND(AVG(between_attempt_seconds), 3) AS mean_preceding_gap_seconds,
    ROUND(MIN(between_attempt_seconds), 3) AS min_preceding_gap_seconds
FROM timed
GROUP BY run_id, stage
ORDER BY run_id, stage;

ROLLBACK;
```

`julianday` has millisecond-scale floating-point resolution, adequate for these
multi-second durations but not a microbenchmark. Check that each run has 100
attempts, zero unfinished attempts, and no negative inter-attempt gaps. Sum the
stage execution and preceding-gap columns: for each run they cover the interval
from its first recorded attempt start through its last recorded finish, except
for the first attempt's setup gap. They omit work before the first attempt and
turn finalization after the last attempt. Compare with the receipt's elapsed
time only as a consistency check; do not call the difference selector time.

`work_attempts.started_at` is written by `begin_attempt`, and `finished_at` by
`finish_attempt`. Successful attempts use outcomes `succeeded` even though the
turn receipt reports user-facing outcomes such as `parse/output_committed`.
Admission review and unit exceptions become non-successful attempts. The
per-stage group gives the actual SQL-stage totals without assigning project
work to parse or vice versa.

## Smallest follow-up if the gap dominates

First use this query; it requires no replay, no new runner, and no parser or
projector execution. If between-attempt gaps dominate, the result justifies a
bounded selector-only profile on a reviewed disposable schema-29 copy. Reuse
the existing offline-selector profiling components and their read-only,
write-authorizer, timeout, source-pin, and scratch-marker checks. The retained
`profile_offline_selector.py` pins the older schema-28 source and marker, so it
cannot be pointed at this schema-29 scratch unchanged. Any successor profile
needs exact current schema/source pins and independent review; keep it to a
single selection with a short hard deadline and no unit execution.

If attempt execution dominates, the query can narrow the next diagnostic to
parse versus project. It still cannot distinguish parser CPU from input-body
reads or SQLite statements, nor projection calculation from generation writes
and commit. Add only bounded phase timers in a reviewed diagnostic on a fresh
copy of the held schema-29 scratch: parser `extract`/`parse`, total parse unit,
total project unit through outer commit, and selection. Do not attach cProfile
to a long drain; instrumentation changes timings. Keep a fixed small cohort and
report instrumented time as diagnostic evidence, not throughput acceptance.

The separate schema-29 trigger diagnostic measured only transactional no-op
updates and excluded durable commits, selection, parser, projection, and
acquisition. It cannot fill these timing gaps. Neither these queries nor a
short profile establish fixed-cohort acquisition service or whether the
remaining 31,758 parse units can be cleared in an operating window.

## Retained query result

The reviewed query ran read-only on the closed scratch at 21:44 UTC after a
small deterministic fixture passed and all three replay services were confirmed
inactive. Both runs had 100 attempts, zero unfinished attempts and no negative
inter-attempt gaps. The compact
[receipt](../../evidence/runtime/schema29-replay-timing-2026-09-17/receipt.json)
retains the exact groups and input hashes.

| Run                    | Execution | Preceding gaps | Covered attempt interval | Receipt elapsed |
| ---------------------- | --------: | -------------: | -----------------------: | --------------: |
| `run_20260917T185409Z` |  12.088 s |      385.990 s |                398.078 s |       402.171 s |
| `run_20260917T191421Z` |   7.605 s |      397.264 s |                404.869 s |       413.860 s |

The gaps dominate both covered intervals. They include selection, service
accounting, admission and derivation capture, so this does not label them as
pure selector time. The result supports the bounded selector-only follow-up on
a fresh candidate-bound schema-29 copy. It does not establish sustained replay,
fixed-cohort acquisition service, deployment or publication.

## Selector-only follow-up

After two independent review passes, the generalized profiler ran once on the
closed candidate-005 schema-29 scratch. It selected the `eepro` source-index
projection in 10.181 instrumented seconds. The database stayed read-only with
zero changes, no worker ran, and a private network namespace plus the helper's
audit hook prevented requests. The hold and all six inactive ordinary units
were unchanged. See the compact
[receipt](../../evidence/runtime/schema29-selector-profile-2026-09-17/receipt.json).

Inventory currentness consumed 3.199 seconds and its desired calculation 3.110
seconds. Event and history currentness were the next visible contributors. The
full currentness call path took 6.816 cumulative seconds; these values overlap
their callers and must not be added. The profile is instrumented and selected
one unit, so it is not an uninstrumented service rate or sustained throughput
result. It also cannot assign the earlier roughly four-second mean gaps entirely
to selection because the gaps include accounting, admission and capture work.

The next performance work should target repeated inventory, event and history
currentness within the already owned read snapshot, using a differential
fixture before another profile. Deployment and operating acceptance remain
separate.
