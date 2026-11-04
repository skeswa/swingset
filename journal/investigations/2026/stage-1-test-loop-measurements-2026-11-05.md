# Stage 1 test-loop measurements

Date: 2026-11-04 to 2026-11-05 UTC  
Type: Investigation notes  
Related plan: [stage 1](../../../docs/plans/postgres-migration/6-stage-1.md)

These notes preserve the measured triggers written before each candidate.
Their open conditions describe those experiments, not the current gate status.
See the [local outcome](stage-1-implementation-2026-11-04.md) for accepted
results and limits. Failed runs remain failures in their sealed receipts.

The accepted default passed all 2,580 cases in 177.61 seconds internally and
177.73 seconds for the whole command. The first warm-profile run instead
failed an obsolete accepted-input reset assertion and exported 157 MB of
diagnostics. Its 183.57 seconds remains a failed gate. Updating that fixture
under D-0274 preserved exact pipeline inputs and every journal assertion;
no additional performance change was made. The final
[developer receipt](../../evidence/runtime/postgres-stage1-2026-11-04/developer-final.json)
records the cold cache state, all warm samples and late failed gates.

## Recorded triggers

The ordering-only repeat passed all 2,548 cases and every receipt check in
183.68 seconds. Parallel clients were balanced at 172.18 to 174.61 seconds;
another ordering repeat is not justified. The [frontend probe](../../../journal/evidence/runtime/postgres-stage1-2026-11-04/core-frontend-trigger.json)
ran five explicitly selected cases in 15.75 seconds, with source and default
cost cache unchanged. Creating its fresh durable physical test provider took
3.95 seconds; all five report readers together took 0.97 and volume cleanup
0.27. These inclusive timings overlap. This measurement triggers investigation
of physical-provider startup for the ordinary core. A remedy must preserve a
separate durable cluster, exact image/settings/roles validation, per-case
leases without pinned snapshots, owned database cleanup and all resource
guards. Lifecycle fault tests and CI still need fresh clusters. Every core
setup, validation and cleanup step remains inside 180 seconds; any warm-cache
preparation and its cold cost must be reported explicitly.

Use a separate managed warm `physical` profile for the ordinary core:
Postgres 17.11, durable named volume, 2 GiB and two CPUs, with its own
loopback port, private credentials and instance identity. Validate exact
container, volume, settings and roles on every use. Establish the database
identity inventory only during known-fresh creation; drift blocks reuse.
PID1 must reap its own Copies on both test endpoints, and the host must
verify the physical inventory is restored before reporting success or
updating costs. A whole-client kill retains its owner ledger and failed
volume; unexpected databases block reuse pending explicit checked cleanup.
Never adopt, reset or prefix-drop unknown state. The parent holds no
per-case lease. Keep fresh lifecycle fault, full-suite and CI factories.

Before the next timing trial, declare one `mise run dev` with the physical
profile absent and the other services/dependencies cached. Record that cold
profile creation within the 30-second readiness target. Then measure the
unchanged default core with all three profiles warm; every validation and
any missing-service startup still counts inside the command. Preserve the
earlier failures. This cache preparation is a new measured workflow, not a
relabeling of an earlier sample.

The same measured run sampled parallel clients every 1.86 seconds: probe
work followed by a fixed one-second sleep. A client may finish during that
sleep. Permit bounded process-completion polling during the existing sleep,
returning promptly once all children finish. Keep the same resource-sample
cadence and every guard while any client is alive; this must not trade away
monitoring for timing. Prove completion, interruption and live-sample behavior
with controlled tests before the next complete trial.

The corrected two-phase default passed all 2,548 cases and every source,
manifest, union and cleanup check, but took 184.27 seconds. The isolated
phase took 2.95 seconds; parallel clients took 168.82, 175.13, 167.08 and
176.01 seconds, with 649.34, 675.19, 644.00 and 682.74 worker-seconds.
That complete verified result automatically refreshed 173 file costs.
The new greedy bins have 662.85 worker-seconds each, suggesting about five
seconds less on the critical client. This measurement triggers one repeat
of the same default command using its automatically refreshed costs. Keep
the source, complete selection, caps and 180-second gate; ordering alone
must still prove the complete result. No manual cost edit is permitted.

The next 2,528-case core took 179.80 seconds, with one failure: the healthy
follow-up in the 30 ms deadline test also expired under parallel load. An idle
boundary trace found no inherited timer or deadline; the healthy derivation
received a fresh bound and completed in 21.40 ms. Earlier unrelated-load
probes reproduced this extra expiry. The [measurement](../../../journal/evidence/runtime/postgres-stage1-2026-11-04/core-latency-trigger.json)
triggers a bounded scheduling change for that timing-sensitive case: assess a
small isolated phase before parallel tests. Its original 30 ms bound and every
assertion must stay. Verify the complete selected, assigned and executed union
across both phases, count a failed isolated phase as failure, and include all
startup, checking and cleanup in the same 180-second core gate. The 19-case
deadline file took 12.53 worker-seconds, so serializing the entire file is not
justified by the measured cost.

A same-image probe collected the full default core, ran that original case
without xdist, and completed owned cleanup in 3.27 seconds (1.77 seconds for
collection, 0.11 for the case). This permits one maintained isolated prologue
before the four parallel clients. Start parallel work only after its exact
assignment, passing outcome and cleanup checks succeed. Concurrent parallel
clients must keep separate PID namespaces; a later phase may reuse a dead
namespace's inode. Keep the existing automatic cost cache unchanged for the
first complete candidate; only a fully verified all-pass union can refresh it.
The probe is not a passing core gate.

The final predecessor-closure source passed all 2,526 core cases and its exact
source, membership and cleanup checks, but took 198.66 seconds. Deleting the
previous successful extended run's owned scratch directory overlapped about
169.63 seconds of that measurement, ending at 09:33:25 UTC. Retain that failed
timing and repeat the same default command after cleanup, with all other heavy
work idle. Keep the selection, caps and 180-second gate; no manual hint, code
or timeout change is justified by this concurrent measurement.

The completed 2026-11-05 core passed all 2,525 cases and every source,
membership and cleanup check, but took 182.84 seconds. Its real restore tests
raised `test_postgres_rebuild.py` from a stale 6.32-second whole-file weight to
30.93 seconds; client times were 164.43, 160.92, 178.38 and 161.65 seconds.
This measured imbalance triggers one run with the maintained executor's
automatically refreshed hints from that fully verified result. Its 173 costs
give four greedy bins of 629.70 worker-seconds each. Keep the exact selection,
resource caps and 180-second gate; ordering changes alone must still pass it.
The final core receipt records both the over-budget measurement and the
refreshed result; raw phase reports stay in the local core job directory.

The full 50,000-file backup guard produced a 222,863,360-byte database stream,
but writer RSS reached 327,778,304 bytes against its unchanged 300 MiB limit.
A frozen-runtime phase profile put the first peak in capture's independent
history verification, before the owner check: export's 50,000 source paths
and result manifest remained alive while verification loaded another manifest.
Capture exit dropped current RSS from 315.8 to 243.4 MB. This
[measurement](../../../journal/evidence/runtime/postgres-stage1-2026-11-04/backup-memory-profile.json)
triggers releasing the exhausted source-path inventory before verification,
and releasing restore's link-only manifest before its independent verification.
Keep every typed audit, file hash, closure check and flush. The original file,
data-size and RSS assertions plus full restored-file hashes still must pass.

Those releases did not finish the memory gate. The production-shaped separate
owner check still measured 314,834,944 bytes, 256 KiB over the same limit, before
and after the RPC. Current-source profiles measured less: instrumentation changes
allocation timing, so they do not establish the remaining peak's cause. The
[latest profile](../../../journal/evidence/runtime/postgres-stage1-2026-11-04/backup-memory-latest-phase.json)
and the real guard failure trigger one bounded candidate: keep only export's
receipt scalars, release its full manifest before independent verification, and
return the verified durable manifest with those same scalars. Also release each
private segment-index bucket only after extraction checked every indexed file
and its digest; later verification and the owner RPC do not read those buckets.
The duplicate
50,000-file maps are observable; whether removing that overlap meets the budget
remains a hypothesis until the original unprofiled guard and full restore pass.

The first complete native extended run passed 4,026 cases and failed 50 in
1,082.84 seconds. The streamed assembly failed in its disposable changelog
spool at 30.4 MiB of its unchanged 32 MB DuckDB budget. The spool maintains
an index over every potentially large ordering key, although its read sorts
only keys and positions and can spill to disk. This failure is the trigger
to remove that redundant ordering index and prove the original full-size
assembly and Python-memory assertions under the same limits. Its compact
[run receipt](../../../journal/evidence/runtime/postgres-stage1-2026-11-04/full-native-first.json)
keeps the exact selection and source binding; traces remain in scratch.
Removing the ordering index alone still failed at 30.3 MiB: while the sort
held its buffers, fetching payloads through a second cursor needed another
256 KiB block. Keep the same budget and spill only keys, positions and payload
offsets through DuckDB; store the payloads in one unnamed scratch file and
read one by offset. The full-size assembly and the 8 MiB Python-memory test
remain the checks. The 40,000-unit fixture also loads in bounded transactions
instead of exceeding the unchanged 60-second production write limit.

The unchanged 33-page completion cohort exceeded five minutes on native
Postgres. A bounded 20-iteration profile on 2026-11-05 took 8.80 seconds:
22,420 domain queries and 14,495 deadline-setting queries spent 6.86 seconds
waiting on Postgres. Per-watch eligibility repeated host, pause and finding
reads within the same selection. This measurement is the trigger to reuse
loaded rows and cache those answers only for that selection's snapshot.
Admission must still recheck current controls immediately before I/O;
coverage and the existing 600-iteration bound stay unchanged. The [controlled measurement](../../../journal/evidence/runtime/postgres-stage1-2026-11-04/test-loop-profile.json)
keeps dependency hashes and outcomes fixed: 7.01 to 3.43 seconds for 20
iterations, with 52.5% fewer driver queries. Full profiles remain in scratch.

The unchanged four-worker core then took 723.72 seconds against its
180-second budget. That failure triggers the narrow timeout-setting work
in the [controlled transport measurement](../../../journal/evidence/runtime/postgres-stage1-2026-11-04/timeout-transport-profile.json).
With every source and dependency hash fixed, its exact 20-iteration trace
used 7,596 timeout-setting statements. Reusing only an identical absolute
deadline and floored remaining millisecond reduced that to 1,947 and cut
test-body time by 18.8%. Each query must still check the clock; Python's
absolute alarm, rollback, commit uncertainty and the original 30 ms test
remain unchanged. Clear the memo at every transaction, savepoint and
session-setting boundary, including raw driver transaction commands.

The complete narrow-placement core still took 372.64 seconds. Its measured
file-flush cost triggers bounded local write concurrency. The [backup flush
measurement](../../../journal/evidence/runtime/postgres-stage1-2026-11-04/backup-fsync-profile.json)
compares four exports of one pinned snapshot: eight pending file flushes
reduced median wall time by 59.9%, with all 816 real fsync calls and every
table, key and retained-file hash unchanged. Keep at most eight duplicate
file descriptors, wait for every flush before writing the final manifest,
and keep manifest and publication flushes synchronous. A failed flush must
leave no completed export.

The complete batched run passed all 2,422 cases in 274.14 seconds, including
cleanup and receipt collection. Setup, calls and teardown totalled 1,792.39
worker-seconds; even perfect eight-worker scheduling needs 224 seconds.
This is the trigger to measure three four-worker clients. Keep each client's
2 GiB RAM-plus-swap cap, the test database's 2 GiB cap and every selected
case. The previous clients peaked at 1.43 and 1.69 GB working memory, with
no swap or OOM; clients plus test Postgres peaked at 3.57 GB together.
Stop the new trial above 7 GiB combined working memory across every local
test, development and lifecycle container, leaving 8 GiB for production and
1 GiB for the VM. Keep the builder stopped and the real-data rehearsal idle.
The complete selection and cleanup still must pass within 180 seconds;
adding workers alone does not satisfy that gate.

That run's per-case phase receipts also predict that hashing files across
three clients puts 706.29 worker-seconds on one client: at least 176.57
seconds before startup and cleanup. Use those measured file costs as
scheduling hints instead. Assign whole files greedily to the least-loaded
client, then start expensive files first; preserve case order within each
file. Every worker still reports the original complete core selection and
its actual assignment. Validate the exact disjoint union and executed cases
independently. An absent cost for a new file gets a positive default; a hint
can change ordering, never membership. Refresh local hints only from a
complete verified run, and keep the measurements in scratch.

The weighted twelve-worker trial finished in 198.83 seconds with 2,433
passes and three failures: a lost-acknowledgement reopen, a 200 ms control
and a 30 ms failure-recording write. All memory limits held; combined local
working memory peaked at 4.92 GB with no swap or OOM. The CPU counters show
314 throttled periods and 27.89 aggregate seconds throttled in test Postgres;
the clients recorded 41.04, 13.10 and 27.72 seconds. The Docker VM has 15
CPUs and this interval used about 10.4 on average. This is the measured
trigger to compare six-CPU caps for each client and test Postgres, keeping
twelve workers, every memory limit and every assertion. The trial must
preserve the exact test-container identity, databases and mounts, leave DEV
and lifecycle caps unchanged, and restore the prior CPU setting unless the
complete result supports adopting it. Throttle counters suggest a cause;
they do not prove that this change fixes either correctness or the budget.

The controlled six-CPU trial passed all 2,436 cases in 195.97 seconds.
Postgres throttling fell to 0.03 seconds; all selected cases and source
bindings matched. Its 2,195.08 worker-seconds require at least 182.92 seconds
with twelve workers before startup or cleanup. Measure four four-worker
clients next, with the same six-CPU caps and 2 GiB RAM-plus-swap limit per
client. The measured combined working peak was 4.79 GB; adding the largest
client's 1.54 GB gives 6.33 GB, below the unchanged 7 GiB stop threshold.
Keep the builder and DEV rehearsal idle and monitor every local service.
The VM has 15 CPUs; measured average use was 10.81. This authorizes the
bounded comparison, not a claim that sixteen workers meet the gate.

The sixteen-worker run still took 190.61 seconds. The
[current worker profile](../../../journal/evidence/runtime/postgres-stage1-2026-11-04/current-recipe-profile.json)
found 194 declaration reads taking 46.75 aggregate worker-seconds in its
45-second diagnostic prefix. Those reads constructed 3,869 import graphs
and parsed literal declarations 13,451 times. This is the trigger to share
one graph and one literal lookup map within each declaration read. A
[controlled full-package pair](../../../journal/evidence/runtime/postgres-stage1-2026-11-04/declaration-reuse-profile.json)
reduced the native function's median from 71.74 to 18.79 ms with exactly
equal scope/version output. Keep reuse local to that call: changed captured
bytes, malformed declarations and mutations of an earlier result must be
checked afresh on the next call. Every bundle/member verification remains;
the complete unchanged core still must meet 180 seconds.

After that change, 2,509 cases passed and the 200 ms control still failed;
the complete run took 185.01 seconds. Its old scheduling hints assigned
698.43 worker-seconds to the slowest client, against 661.00 for a balanced
assignment of the new measurements. This is the trigger to refresh the
trial's hints from fully passed files in that completed, source-bound run;
the failed control file keeps its last all-pass measurement. This bootstrap
changes ordering only and cannot pass a gate or promote a failed result.
The maintained cache still updates only after complete verified success.
Fix and keep the original control deadline before the next complete check.

The maintained default passed all 2,513 cases but took 184.41 seconds.
This [measured failure](../../../journal/evidence/runtime/postgres-stage1-2026-11-04/maintained-core-first.json)
triggers the remaining per-call bundle-verification work. A
[controlled full-package pair](../../../journal/evidence/runtime/postgres-stage1-2026-11-04/member-validation-profile.json)
found 3,057 member references but only 202 unique paths. Computing each
member digest once reduced median validation from 60.36 to 28.08 ms, with
identical accepted input digests. Reuse only the computed digest within that
call: compare every group's expected value, verify every runtime member,
and retain declaration validation. A bad digest in a later group and changed
bytes in a later call must still fail. No cache survives the call.

The next maintained run still took 186.17 seconds. Its current profile puts
runtime file capture at 39.52 aggregate worker-seconds; bundle validation
fell to 9.94. The [fresh traversal pair](../../../journal/evidence/runtime/postgres-stage1-2026-11-04/fresh-capture-profile.json)
measures the remaining filesystem work: pruning bytecode directories before
descent and using fresh directory-entry types cut full capture from 38.43
to 24.86 ms. Every one of the 519 file opens remained fresh, with identical
523-member output bytes and ordering. This is the trigger to replace that
walk, preserving file symlinks, skipped special files, directory-symlink
behavior and directories whose names end in `.pyc`. Do not cache file bytes.

The unchanged control CLI case still missed its 200 ms servicing deadline.
A [fresh-process profile](../../../journal/evidence/runtime/postgres-stage1-2026-11-04/control-selector-profile.json)
found that listing registered source names imported all adapters, including
the PDF parser, inside that deadline. Four concurrent original cases spent
62 ms each importing adapters. Reading the existing explicit registry names
instead reduced median servicing from 88.45 ms to 27.27 ms in the controlled
pair, with the original assertions, database queries and identity checks.
This is the trigger to use registry names for selector validation. Keep the
200 ms deadline and every role, UUID, environment and selector check; the
complete suite remains the performance gate.
