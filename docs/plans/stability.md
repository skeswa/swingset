# Run unattended: contain failures, pace global work, collect what is dead

The worker must keep improving the dataset without an operator. From
2026-09-21 to 2026-09-24 it did the opposite: one bad row failed every build,
each failure left 235 MB behind, a blocked history write closed acquisition,
and every cycle still said `failed=false`. The
[investigation](../../journal/investigations/2026/build-failure-and-history-deadline-2026-09-24.md)
has the measurements. This plan fixes that incident and states the rules that
stop the same pattern from coming back.

Superseded as the direction of work by the [target architecture](architecture.md)
(2026-09-25), which turns these rules into ten invariants and a phase plan.

## The rules

Each rule answers one measured failure. New code follows them; review checks
them.

1. **Failure stays local.** A defect withholds the smallest unit it touches,
   such as an event or a unit. It never withholds the whole release or the
   whole queue. _Was:_ one callback mark failed every build (D-0199).
2. **A failed attempt leaves nothing durable.** Proofs, manifests and
   generations are written only after the thing they describe exists.
   _Was:_ 146 orphan closures, 33.9 GB (D-0199, D-0200).
3. **Nothing waits forever, and nothing retries blindly.** Every non-success
   outcome has a retry time or an alert. Retries back off. _Was:_ a
   deadline overrun was `blocked` with no retry path (D-0198).
4. **Global units run on a clock, not on every change.** A unit that reads the
   whole graph, such as the release build or the history projection, runs at a
   fixed interval. _Was:_ two builds a cycle, and history re-queued by
   every sheet (D-0199).
5. **Health means progress.** A cycle that failed any stage reports
   `failed=true`. After a resume or deploy, the check is fetched pages and
   settled units, not finished cycles. _Was:_ 96 of 100 failing cycles reported `failed=false`
   (D-0199, D-0190).
6. **State is bounded by a collector, not by raising the cap.** Anything the
   pipeline writes has a rule that removes it once nothing names it. _Was:_
   three cap raises in two days (D-0200).
7. **Acquisition depends only on facts that decide what to fetch.** Derivation
   health, builds and publication never gate fetching. _Was:_ the history
   projection gated score-sheet fetches (D-0198).

## Candidate 018

Candidate 017 plus the changes below. It is one Python switch, so it costs one
whole-graph re-derivation (below).

| Change                                                                                       | Rule | Record | Tests                                                                                   |
| -------------------------------------------------------------------------------------------- | ---- | ------ | --------------------------------------------------------------------------------------- |
| Row closure resolves events through rejected parents; dangling references withhold the event | 1    | D-0199 | `tests/build/test_closure_event_alternatives.py` (two new cases)                        |
| Closure proof retained after the candidate files; build recipe names it by digest            | 2    | D-0199 | `tests/build/test_completion_validation.py` (three new cases)                           |
| A write-deadline overrun is `transient`, 15 min doubling to 6 h                              | 3    | D-0198 | `tests/test_write_deadline.py`                                                          |
| History gets a 300 s write allowance (replaced by D-0217: one 60 s alarm for every write)    | 3    | D-0198 | `tests/test_write_deadline.py`                                                          |
| `build_interval_seconds` 6 h, `history_interval_seconds` 12 h                                | 4    | D-0199 | `tests/test_cycle_controls.py`                                                          |
| A build error sets `failed=true`                                                             | 5    | D-0199 | `tests/test_cycle_controls.py`                                                          |
| A closure validation error is contained like a build error                                   | 1    | D-0199 | `tests/test_cycle_controls.py`                                                          |
| Local-page reads get their own time window; completion reads its proof once                  | 1    | D-0199 | `tests/build/test_event_local_coverage.py`, `tests/build/test_completion_validation.py` |
| `gc --closures` / `gc --collect-closures`                                                    | 6    | D-0200 | `tests/test_closure_collection.py`                                                      |
| A pending history no longer closes phase two                                                 | 7    | D-0198 | `tests/test_history_projection_gate.py`, `tests/test_history_inventory.py`              |
| `finalize_history` rewrites only changed associations                                        | —    | —      | history suites                                                                          |

**What deploying it costs.** The two Python re-queues on 2026-09-23 each
re-parsed about 30,000 snapshots and re-derived about 31,000 projections in
about four hours, adding about 0.8 GB. The history run and a full link pass
follow: 2,932 units at about 6 seconds each, roughly five more hours, plus
history's ~115 MB. Phase two stays closed until the re-derived dancer and
inventory units settle, about four hours after the switch. Fetching then starts
without waiting for links. The freed pages from collection absorb the growth.

## Candidate 019

Candidate 018 ran clean for seven hours, then from 2026-09-25 01:32 UTC most
cycles died about 55 seconds in, before acquisition. The event-site review,
newly reachable once history stopped closing the gate, asked the ten-second
projection question once per site inside a 45-second write. Candidate 019
answers it once per write and contains any overrun in a pre-acquisition stage
(D-0201, rule 1). It is a Python switch, so it pays the whole-graph
re-derivation again: about 2.5 hours of parses at the rate candidate 018
measured, then the link pass.

## Production sequence

Each step names its check and its way back. Nothing here publishes; D-0189
keeps publication off until identity output is recovered.

1. **Back up (done 2026-09-24 15:10 UTC).** A byte copy of the paused
   `state.sqlite`, 64,084,664,320 bytes, sha256 `f3ca54f3…0330f`, lives outside
   the worker quota at `~/swingset-offload/state-20260924.sqlite` on the Mac.
   _Rollback for every later step:_ stop the units, copy it back, verify the
   hash.
2. **Independent review (done).** A second agent reads this diff, the rehearsal
   receipt and this sequence, and writes a verdict. Steps 3 and 5 wait for it.
3. **Switch to candidate 018 under the hold (done 17:36 UTC).** Build the system from a deploy
   tree with `nixos-rebuild switch`. The operator hold keeps every timer from
   starting a cycle; leave it in place through step 5, since a switch can
   restart stopped timers. _Check:_ the service store path, and that
   `swingset gc --help` from the service environment lists `--closures`. _Rollback:_ switch back to the 017 system
   generation; the schema is unchanged at 32.
4. **Plan the collection (done; digest matched).** Run `gc --closures` with the service
   environment. _Check:_ the digest equals the rehearsal's (`5aa40356…`), since
   the state is unchanged: 146 of 157 proofs, 33.9 GB, 11 kept. If it differs,
   read why before collecting.
5. **Collect, then reclaim (done 17:38 to 18:01 UTC; 29.41 GB).** Run `gc --collect-closures <digest>`, then
   `gc --reclaim`. Before the reclaim, read `swingset-disk` and require at
   least 70 GB free for the rewrite's temp file and log; run with
   `TMPDIR=/var/tmp`. If the rewrite fails, run `PRAGMA
wal_checkpoint(TRUNCATE)` first. _Check:_ the receipt, the file size (rehearsal:
   64.08 GB to 29.40 GB), `PRAGMA quick_check`, and that the baseline's public
   closure still hydrates. _Rollback:_ step 1's copy.
6. **Resume (done 18:05 UTC).** Remove the hold file, run `swingset resume --all`, and start the
   cycle, backup, summary and scratch timers. _Check:_ the first cycle's
   `recovered_admissions` settles the build killed at 14:53 UTC, and the pause
   leaves `pausing`.
7. **Watch progress, not cycles.** For 24 hours, read the checks below.
   _Rollback:_ write the hold file and stop the timer. A bad result reverts
   through step 3, or step 1 when data changed.

## Done means

Read from the `runs`, `snapshots` and `scheduler_requests` tables, never from
cycle completion alone:

- No cycle grows the file by more than its own work. An idle cycle grows it by
  less than 10 MB, where it grew 471 MB before.
- `project/history/all` settles within 12 hours of the switch, and again
  within 12 hours of any later change.
- After the re-derivation settles, `snapshots` rows with `via='wayback'` and a
  platform URL (EEPro, scoring.dance, WDR) appear, with matching
  `scheduler_requests` rows for `web.archive.org`. CDX rows in
  `archive_captures` do not count.
- The first build after the switch succeeds or withholds events. A cycle with
  `build_error` reports `failed=true` and raises a watchdog alert.
- The link backlog drains once per history interval.

## What this plan does not fix

- **Every Python change still re-derives everything.** The
  [derivation invalidation plan](derivation-invalidation.md) removes that.
  Until then, batch Python changes. Candidate 018 is this week's one switch.
- **Each successful build still retains one proof of about 235 MB.** It stays
  as long as its build generation names it, which is permanent today. At the
  6-hour build interval, and with builds reused when nothing changed, that is
  at most about 1 GB a day. The fix is to name only live candidates, the
  baseline and the newest build generation. Measure first; queue it after a
  week of receipts.
- **Links depend on the whole history generation.** One history run re-queues
  all 2,932 links. The fix is a per-event history dependency, which belongs in
  the derivation invalidation plan.
- **History still writes ~200k rows per run.** The 300-second allowance
  covers it at twice a day. Moving `registry_placements.event_id` out of the
  history output removes 98% of those rows. Phase 2 stores them in chunks
  and drops the allowance (D-0217).
- **A build peaks near 5.5 GB** against the worker's 8 GB memory limit
  (rehearsal on the Mac, final source). Watch the first builds for an OOM kill.
- **Found by the independent review, not yet fixed** (verdict in the
  production receipt): `gc --reclaim` checks free space with `statfs`, which
  inside OrbStack reads the Mac volume, not the worker quota, so check
  `swingset-disk` by hand first (at least 70 GB free, `TMPDIR=/var/tmp`). A
  deadline backoff restarts when history's inputs change, so a history that
  overran even 300 seconds would retry once per changed input. Pacing
  history holds `registry_placements.event_id` up to 12 hours, which can
  cost a few extra registry confirmation requests.
- **Two builder checks still fail the whole release** rather than one event:
  callback counts that disagree with their marks, and non-contiguous places.
  Nothing known triggers them today; route them through the same withholding
  when one does, or sooner.
- Identity output recovery and publication stay in the
  [data quality plan](data-quality.md).

## Receipts

- [Candidate 018 switch, collection, reclaim and resume](../../journal/evidence/runtime/candidate-018-2026-09-24/receipt.json)
- [Pause](../../journal/evidence/runtime/worker-pause-2026-09-24/receipt.json)
- [Backup and rehearsal](../../journal/evidence/runtime/stability-rehearsal-2026-09-24/receipt.json)
