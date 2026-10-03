# Active plans

Plans describe remaining work and how to prove it is complete. They do not
establish that a feature is deployed. Start with [current status](../status.md)
for the latest retained operating evidence.

| Goal                                                          | Plan                                                              | Read first                                      |
| ------------------------------------------------------------- | ----------------------------------------------------------------- | ----------------------------------------------- |
| **Active, all other plans paused:** scale and tolerate faults | [Target architecture](architecture.md)                            | Invariants, then the phase plan                 |
| Run unattended without stalling                               | [Stability](stability.md)                                         | The rules, then the production sequence         |
| Recover identity output and missing event metadata            | [Data quality recovery](data-quality.md)                          | Measured regression and release checks          |
| Add historical results while improving recovery               | [History and recovery sequence](history-and-recovery.md)          | Stage order and gates                           |
| Preserve corrections and recover safely                       | [Recovery rollout](recovery/README.md)                            | Work packages and acceptance scenarios          |
| Bound operational storage without losing history              | [Bounded state and durable archive](bounded-state-and-archive.md) | Stages, gates, and known limits                 |
| Stop re-deriving output that has not changed                  | [Derivation invalidation](derivation-invalidation.md)             | Measured waste, then the ordered steps          |
| Fill historical gaps from event sites                         | [Historical source collection](historical-sources.md)             | Source restrictions and review requirements     |
| Move to PostgreSQL and Dokploy                                | [Migration](postgres-migration/README.md)                         | Approach, then the relevant implementation step |

The [project milestones](milestones.md) define dataset outcomes across these
plans. Earlier rollout documents live in the [archive](../../journal/archive/README.md).

## Reading older work IDs

- **V1–V7:** stages in the history and recovery plan.
- **H1–H18:** recovery and data-quality changes in the recovery rollout.
- **WP:** an implementation work package. Early packages are in the
  [first implementation plan](../../journal/archive/v1-implementation-plan.md);
  historical packages are in the [backfill reference](../reference/backfill.md#work-packages).
- **G1:** the controlled registry probe rehearsal.
- **M:** a milestone; the PostgreSQL plan has its own explicitly scoped M0–M9 steps.

Use descriptive titles in new writing and retain these IDs only to connect
existing plans and evidence. A new proposal belongs in a
[decision record](../../journal/decisions/README.md) when it changes a lasting choice.

## Paused plans

On 2026-09-25 the owner paused current work to implement the
[target architecture](architecture.md). The plans above stay as references
and resume when a phase of that plan says so. Operating the worker safely is
not paused.

## Maintenance queue

Maintenance gets one session a week ([D-0175](../../journal/decisions/0175-fewer-records-receipts-not-bundles-and-tiered-review.md)).
Queue side quests here instead of starting them; take the top item in the
weekly session.

1. [Bounded state](bounded-state-and-archive.md), remaining steps: parked until
   a measurement receipt shows the live database over 20 GB or the worker disk
   under 100 GB free. The one open item is the A0/A1 source JSON compression
   benchmark (attempt 003) and its decision, which the owner asked to finish.
2. [PostgreSQL and Dokploy migration](postgres-migration/README.md): a target,
   not started.
3. Step Right archive-only index bootstrap and `recognize` rules, then DCN's
   modern payload (M4): the acquisition paths V5's new sources still lack
   ([plan note](history-and-recovery.md#event-completion-follow-up-in-v6)).
   This is release work, queued here only until the parse backlog drains.
4. [Derivation invalidation](derivation-invalidation.md): measured and planned
   on 2026-09-23; steps 1, 2 and 5 are implemented offline as the target
   architecture's phase 1, not deployed, to ship in one switch. Until then one
   edited line changes every unit's recipe, so every deploy re-parses 32,522
   snapshots and re-derives the whole graph; 99.98% of the replacing
   generations produce byte-identical output. Batch every Python change and
   prefer none. Its step 3 collection of unnamed closure proofs shipped early
   in candidate 018 (D-0200); superseded row references and parse evidence
   remain. The plan's trigger is a full day of clean phase-two cycles. This
   replaces the former items 4 and 8, which are folded into it
   ([investigation](../../journal/investigations/2026/derivation-invalidation-2026-09-23.md)).
5. A dismissed `event_alias` finding should also settle the event's dates. Today
   the dismissal only hides the finding: the inventory projector skips the
   ambiguous listing, so the row keeps whatever an earlier pass wrote. After
   the 2026-09-21 review, `2025-01-countdown-swing-boston` still carries the
   stale "(Unconfirmed)" span of Dec 26 to 31 2024, and
   `2016-09-sunny-side-dance-camp` stays month-only although the reviewer's
   note names Sep 4 to 11 2016. Let a dismissal carry the chosen span, or
   fall through to the widest current listing
   ([projector](../../src/swingset/project/history.py), D-0187). Release work
   for the date item in the [data quality plan](data-quality.md).
6. Split the checkpoint transport archive across files. The remote backup
   refuses an archive over 20 GiB, and the live database passed 24 GB on
   2026-09-22, so remote checkpoints fail until the archive shrinks; the local
   copy is the only backup meanwhile ([D-0192](../../journal/decisions/0192-a-failed-backup-waits-for-the-next-timer-run.md)).
   In the same change: check the archive size before copying anything,
   reuse a local checkpoint younger than an hour instead of copying again,
   and link the content-addressed blobs into a new checkpoint instead of
   rewriting them, since only the database changes between copies. Touches
   backup and restore, so it takes the full suite.
7. Decode zstd replays from the Wayback Machine. Two 2026 captures of
   `worldsdc.com/events/` were archived with `content-encoding: zstd`; the
   replay path stored the still-encoded bytes as the body and the parser saw
   no rows. The client only decodes gzip
   ([limits](../../src/swingset/fetch/limits.py)). Add a zstd decoder with the
   same byte ceilings, re-parse the two captures, then drop their four
   dismissal rows from `finding_reviews.csv` (2026-09-22).
8. Human identity review website (H17) and the JesAnn history page as a site:
   deferred, see [D-0055](../../journal/decisions/0055-defer-human-adjudication-to-a-review-website.md).
9. Bring the core test run back under three minutes. On 2026-09-26 it took
   194 s for 1,264 tests alone on the Mac (228 s in the phase 1 review; about
   203 s at phase 0). Most of the rise is new tests in existing core files.
   Profile with `--durations=30` and move exhaustive parameters to extended,
   keeping each behavior the [testing guide](../guides/testing.md#what-the-core-protects)
   lists in core; the 33-page cohort alone takes about 20 s. After phase 1's
   last review round: 1,277 tests in 193 s. No phase 1 test is among the 40
   slowest (each under 0.5 s), so moving them would not help; the 22
   `test_restart_at_pipeline_checkpoint` cases take about 2 s each (45 s).
   Accepted as debt for phase 1's switch (at close, 2026-09-27: 1,304 tests in 196 to 255 s);
   both slow groups are behaviours the testing guide keeps in core, so the
   fix is faster fixtures, not a smaller selection. After phase 2
   (2026-09-27): 1,492 tests in 245 s. Its seven new core files add 122
   cases and about 31 s; 21 s of it is `test_loop_isolation.py` and
   `test_loops.py`, which run whole loop ticks, some in subprocesses.
   After phase 2's second review fixes (2026-09-28): 1,537 tests in 284 s
   on the Mac; the busy-writer publication test no longer waits out 5 s.
   At phase 2's close (2026-09-29): 1,537 tests in 252 s.
   At phase 3's offline close (2026-09-29): 1,664 tests in 344 s. Two
   tests take 41 s of it: the armed read-set run of the pipeline fixtures
   (`test_read_sets.py`, 22 s), which the core keeps as the recorder's
   acceptance, and the 33-page cohort (20 s).
   At phase 3's close (2026-10-02): 1,736 tests in 340 s, after the fix
   rounds moved fast regression tests into core; still about twice the bar.
   At phase 4's close (2026-10-03): 1,938 tests in 440 s (795 to 1,055 s
   on a loaded Mac); phase 4 added `test_storage`, `test_body_packs`,
   `test_output_packs`, `test_collector`, `test_storage_migration` and
   `tests/backup/*` to core.
   At phase 5a's offline implementation (2026-10-03, before its rehearsal):
   1,954 tests in 466 s; 5a added `test_phase5_migration` and
   `test_origin_candidates_tool` to core, and `test_event_site_review_tool`'s
   write-nothing and unaccepted-year cases (the rest of the file is
   extended). At phase 5's offline close (2026-10-03, after 5b): 1,945
   tests in 489 s; removing the cycle did not lower it. The `event_*`
   removal (open decision 2) deletes six whole core files. After phase 5's
   review fixes (2026-10-04): 1,951 tests in 518 s, beside a rehearsal.
   At phase 5's close on the final tree (2026-10-04): 1,955 tests in 524 s,
   beside a rehearsal.
10. Bound the memory of the publication report's closure check. It holds a
    whole closure (I10): `swingset summary` peaked at 4.5 GB on the 35 GB
    copy, 4.4 GB of it this check, under the unit's 5 GB cap, and the file
    grows about 5 GB a day. Stream the closure instead of holding it, and
    record the worker's `MemoryPeak` for `swingset-summary.service` first
    (the phase 1 post-check has it). `[summary-failed]` fires if the cap is
    hit before then.
11. Fix the nondeterministic `project/source_event` output that phase 1's
    cutoff flagged (`nondeterministic_output`, five cycles on 2026-09-29): the
    same inputs produced different rows, so find the unordered iteration or
    clock read and pin it with a test ([receipt](../../journal/evidence/runtime/architecture-phase1-2026-09-29/receipt.json)).
12. Move the worker to an always-on host. Production is a VM on a laptop: on
    2026-09-30 the Mac slept on battery from about 21:00 to 18:00 UTC, so
    cycles, timers and a workflow agent stalled for 21 hours. Until the move,
    keep the Mac on power with the lid open, or run `sudo pmset -a disablesleep 1`: `caffeinate` does not stop clamshell sleep on battery, and on 2026-10-02 the lid closed at 19:00 UTC and the worker froze for 8.7 hours. Make
    host independence a trigger for the [PostgreSQL and Dokploy](postgres-migration/README.md)
    move (phase 6) rather than waiting for data size; it also gives off-machine
    backups.
13. Stop backpressure counting superseded parse rows. They keep their
    `pending_work` row and get no `work_queue` row, and `schedule/fairness.py`
    counts a missing queue row as runnable, so the thresholds stay raised
    (D-0230). Exclude them, or clear them, then return `pending_parse_items`
    and `pending_parse_bytes` to their defaults.
14. Phase 4's carried-forward minors: read races in the accessor, the
    retry file's `xb` open, body packing's stall and catch, the drill's place
    in the budget, sets that list `missing`, lost-object scans that grow, two
    noisy alerts, and the surviving mutants. The list and its order are in
    the [architecture plan](architecture.md#9-migration-plan), phase 4
    ("Carried forward"). Take them after phase 4's deploy, in that order.
15. Give `scheduler_requests` a collector. Nothing deletes its rows, and the
    admission prune keeps every request row it names (14,546 of 14,570 on
    the 09-29 copy), so the ledger grows by about 1,800 rows a day and each
    prune batch scans the kept rows (D-0238). Delete rows older than the
    quota window the scheduler reads, then their admissions; the maintain
    record's `admissions_rows` shows the growth until then. Trigger: the
    watchdog's `[admissions-growth]`, maintain's `admissions_rows` over
    200,000 (about 100 days of growth from the 09-29 copy). This is a
    collector, not the removal open decision 2 is about.
16. Phase 5's carried-forward minors: drain liveness from a lease that is
    never renewed, errors after a pause commits, the orphan sweep's read
    race, CDX admissions without a run, cycle-run admissions, publication
    uploads in `[admission-stuck]`, a re-created requirement's opening
    time, the site-review tool's immutable open, and the card's
    site-review gap count. The list is in the
    [architecture plan](architecture.md#9-migration-plan), phase 5
    ("Carried forward"). Take them after phase 5's deploy, in that order.
