# Why the worker stalled: one bad release row, a monolithic history write, and a gate between them

Date: 2026-09-24, UTC  
Type: Investigation  
Topic: Build, acquisition, retention  
Related plan or decision: [Stability plan](../../../docs/plans/stability.md),
[D-0198](../../decisions/0198-history-no-longer-gates-acquisition-and-deadline-overruns-retry.md),
[D-0199](../../decisions/0199-contain-build-failures-and-pace-global-units.md),
[D-0200](../../decisions/0200-collect-release-closure-proofs-nothing-names.md)

## Summary

Three defects together stopped all progress, and the worker still reported
`failed=false`:

1. **One callback mark failed every release build** from 2026-09-21 16:34 UTC.
   Row closure withheld Budafest 2020, but it looked up each row's event only
   through parents that were still kept. The `open-strictly` contest and the
   `L-502` entry had both been rejected, so the mark named no event, survived,
   and tripped the builder's check.
2. **Each failed build left a 235 MB proof behind.** The build retained its
   private release closure before writing files. Each closure carries its own
   cutoff, so nothing deduplicated. Two attempts per cycle made the 471 MB per
   cycle. 146 unnamed proofs hold 33.9 GB of the 64.08 GB file.
3. **History overran its write bound and closed the acquisition gate.** The
   `project/history/all` unit stores ~200k rows in one write. At 08:01 UTC it
   ran 64 seconds against a 45-second bound and was marked `blocked`, and a
   blocked unit waits for an input change. A pending history closed phase two,
   so no platform score sheet was ever fetched.

The 179 Wayback snapshots in the database are all calendar pages (worldsdc.com
and swingdancecouncil.com). None is an EEPro, scoring.dance or WDR score sheet.

## How the measurements were taken

Read-only queries on the paused worker, then a byte-exact copy of the database
(sha256 `f3ca54f3…0330f`, 64,084,664,320 bytes, taken 15:07–15:10 UTC under the
hold) with the rest of the state directory on the Mac. Rehearsals ran on APFS
clones of that copy, with the deployed candidate 017 source and with the
repaired source. The Mac's interpreter differs from the worker's, so a clone
re-queues `recipe/runtime`. Closure selection reads only generations created
before the cutoff, so the re-queue does not change the release a build selects.

## Findings

**Growth per cycle.** From 08:03 UTC every cycle ran no work stage and grew
the file by 471 MB. Each cycle admitted two builds about five minutes apart:
one at the start of the offline phase and one after it. Each stored one
235,394,776-byte closure in `derivation_dependency_sets`. Consecutive closures
differ only in `cutoff` and the event-coverage digests that depend on it. The
deployed code, rehearsed on the copy, failed the same way in 261 seconds. It
grew the file by 59,673 pages and peaked at 5.9 GB resident. The worker
limit is 8 GB.

**Where the file is.** 157 release closures hold 36.3 GB. Eleven (2.39 GB)
are named by a build generation or a candidate directory, the baseline among
them. The other 146 (33.9 GB) are named by nothing. The eight completed `build/release` generations also
embed a copy of their closure in `recipe_json` (217–223 MB each).

**Why the mark survived.** On the copy, row closure rejected 6,471 callback
marks, 998 entries, 8 contests and 20 rounds of Budafest 2020, and omitted
1,135 events in all. The kept `L-502` prelim mark resolved its event through
its kept round, then the missing contest, then the missing entry, and got
nothing. The live canonical tables are consistent: `L-502` exists, and no
mark or round is orphaned there. The defect is only in the release's row
reconstruction.

**Why history overran.** Profiled on the copy with the bound lifted, the
deployed code took 82 seconds, 55 of them inside the write. `finalize_history`
took 2 seconds. Storing the 201,073 output rows took 47 and the commit 24.
Once blocked, `finish_attempt` refused any retry time, and the only operator
retry command covers parse units.

**Why acquisition never started.** `phase_two_allowed` returns false while any
inventory-capable projection is pending, and history counted as one. With
derivations on, history writes only `registry_placements.event_id`,
`events.coverage_tier` and `coverage`; the year digest reads none of them.
Every fetched sheet re-queues history, so even a successful history run would
have closed the gate again at the next sheet.

**Other stalled work.** The 504 pending parse units are all parked: 290 need
review, 194 are superseded, 9 failed, 8 wait for inputs, and 3 have an
unsupported page kind. The 2,932 link units last ran at 2026-09-23 11:49 UTC;
they wait on history.

**What a Python deploy costs.** The two re-queues on 2026-09-23 (05:45 and
12:24 UTC) each re-parsed about 30,000 snapshots and re-derived about 31,000
projections in seven 40-minute cycles, about 4 hours. The file grew
2.44–2.50 GB in each window, of which 1.65 GB was seven failed-build closures,
so the re-derivation itself cost about 0.8 GB. After it, history re-runs and
all 2,932 links re-derive, about 6 seconds each, roughly 5 hours.

## Conclusion and follow-up

The repairs are in the [stability plan](../../../docs/plans/stability.md) and
its three records. The rehearsal on the copy with the repaired source:

| Check                           | Deployed code (017)                 | Repaired code                                 |
| ------------------------------- | ----------------------------------- | --------------------------------------------- |
| Release build                   | Fails on `L-502` after 261 s        | Succeeds; Budafest withheld with 1,134 others |
| Proof retained by that attempt  | 235,394,776 bytes, named by nothing | Only the successful build's, named by it      |
| Build generation `recipe_json`  | 217–223 MB                          | 2.3 MB                                        |
| History unit, production limits | Overran 45 s, then blocked          | Succeeded; 47 s write under a 300 s allowance |

Two more defects surfaced while rehearsing and are fixed in the same
candidate. The 30-second budget for local-page evidence started before the
roughly 24-second closure walk, so the same build passed or failed with
machine load (`local_page_support_unverifiable`). The resulting `ClosureError`
was not a `BuildError`, so in a cycle it would have aborted fetching too.
Completion also re-decoded and re-hashed the 235 MB proof at each boundary,
which pushed resident memory to 7.4 GB against the worker's 8 GB limit; it is
now verified once (5.5 GB peak).

The collector rehearsal is recorded in the plan's receipts section.

## Evidence and reproduction

- [Pause receipt](../../evidence/runtime/worker-pause-2026-09-24/receipt.json)
- [Backup and rehearsal receipt](../../evidence/runtime/stability-rehearsal-2026-09-24/receipt.json)
