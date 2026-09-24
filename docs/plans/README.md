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
   on 2026-09-23, not started. One edited line changes every unit's recipe, so
   every deploy re-parses 32,522 snapshots and re-derives the whole graph;
   99.98% of the replacing generations produce byte-identical output. Its step
   3 collection of unnamed closure proofs shipped early in candidate 018
   (D-0200); superseded row references and parse evidence remain. The plan's
   trigger is a full day of clean phase-two cycles. Estimated at six to nine
   working days, about two calendar weeks; write steps 1, 2 and 5 offline
   while the gate is closed and ship them in one switch, so the whole-graph
   re-derivation is paid once and never again. Until then, batch every
   Python change and prefer none. This replaces the former
   items 4 and 8, which are folded into it
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
   backup and restore, so it takes the full suite. Also make the watchdog
   call `logger` before it writes to the alert log, so a full disk cannot
   silence it.
7. Decode zstd replays from the Wayback Machine. Two 2026 captures of
   `worldsdc.com/events/` were archived with `content-encoding: zstd`; the
   replay path stored the still-encoded bytes as the body and the parser saw
   no rows. The client only decodes gzip
   ([limits](../../src/swingset/fetch/limits.py)). Add a zstd decoder with the
   same byte ceilings, re-parse the two captures, then drop their four
   dismissal rows from `finding_reviews.csv` (2026-09-22).
8. Human identity review website (H17) and the JesAnn history page as a site:
   deferred, see [D-0055](../../journal/decisions/0055-defer-human-adjudication-to-a-review-website.md).
