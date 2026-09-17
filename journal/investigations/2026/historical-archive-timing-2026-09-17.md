# Bounded historical Archive timing, 2026-09-17

The local implementation now records eligible waiting for verified historical
Archive offers. It adds schema 29. It is not included in frozen runtime 003,
which was deployed and migrated separately to schema 28. No new historical
request, owner year acceptance, page-kind admission, publication or production
operation was performed by this implementation task.

## Implemented boundary

The existing dispatcher brackets its ordinary plan/candidate work with a semantic
dependency revision and the existing control revision. An immutable proof carries
the exact existing watch and offered capture, source kind, current connection and
run, history floor and expiration. It is handed only to currently observed
watches, at most 256. No candidate, year inventory, capture or parent is scanned
again by resumed timing samples.

The new revision fences inserts, updates and deletes across dispatch and
inventory dependencies, including source-unit selection, parent generation
changes, snapshots and parse status, captures, mappings, acceptance, findings,
inputs and work attempts. Controls keep their separate restricted writer and
revision. Host and scheduler accounting remain independently checked gates.
Timing observations do not increment the semantic revision.

At most 64 pending parse units and their latest retry metadata establish the
earliest future retry boundary. This is conservative across unrelated work.
Unknown, oversized or malformed metadata withholds proof. The normal dispatcher
retains its existing plan/candidate work; the added handoff does not duplicate
that work or claim the whole existing dispatcher has a new bounded scan.

The observer checks the fence both when opening and closing intervals. A
semantic change makes the crossing interval unknown. The dispatcher closes
observation before its scheduling transaction and resumes it in `finally`,
including a busy recheck or exception. The current ordinary host, robots, source,
retry, hold, backpressure and shared allowance checks remain necessary.

## Validation

The source-bound [focused receipt](../../evidence/runtime/historical-archive-timing-2026-09-17/local-check-001/checks.json)
passed 27 new tests, Ruff, and mypy over six source files with unchanged before
and after hashes. A separate broader run passed 140 tests in 14.71 seconds across
historical timing, ordinary timing, fetch eligibility, platform backfill, origin
dispatch, cycle, H14 acceptance and timing rendering. These are separate runs,
not a combined test count or a complete repository suite.

The new cases use actual retained-plan offer checks and admitted synthetic parent
controls. They cover:

- Explicit year, source-kind, source-enable, mapping and watch-retry gates.
- Paid-budget denial without a request or refund, and an actual operator hold.
- Capture deletion/change, parent selection/revocation, parser status, review and
  year-inventory changes invalidating previously positive proof.
- An earlier failed parent capture becoming pending solely when its retry time
  arrives, invalidating a later round offer without a database write.
- Correct closed-interval cutoff at that retry, plus producer, opening and closing
  races that cannot accrue a stale positive interval.
- Wrong run/connection, over-limit watched or pending populations, and invalid
  retry timestamps withholding proof.
- SQLite read denial for archive, parent and year tables during resumed samples,
  demonstrating that samples do not repeat those scans.
- A real mocked HTTP failure through historical dispatch preserving the closed
  successful-progress lower bound across a fresh observer phase.
- The direct dispatch busy recheck resuming timing on its early-return path.

One existing fetch-eligibility expectation changed from an unbounded historical
year/mapping diagnosis to `historical_dispatch_proof_required`. Source acquisition
still performs its full gates. Timing no longer reconstructs those gates without
a dispatcher proof.

## Remaining operating gates

Global invalidation deliberately loses coverage for unrelated events until a
later ordinary offer pass. Measure the additional trigger write cost on retained
replay before any schema-29 deployment; no throughput estimate is claimed here.
A fresh source freeze, full validation, schema-28 migration/preservation rehearsal
and reviewed rollout remain required. Schema-28 operational helper receipts stay
pinned to their reviewed runtime and must not be relabeled as schema-29 checks.

Origin, interpretation, whole-event, legacy and continuous fleet eligible time
remain unknown. Historical years and new kinds still require their existing
independent acceptance. This local slice does not close V6. See
[D-0070](../../decisions/0070-fence-historical-archive-timing-proofs.md) and the
[timing contract](../../../docs/reference/event-timing.md).
