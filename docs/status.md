# Current status

Updated 2026-09-24 14:54 UTC. Five sections; each line links to one receipt.
History lives in the [journal](../journal/README.md); the last long-form page
is [archived](../journal/archive/status-2026-09-18.md).

## Published

- Worker-acknowledged public dataset [`138352e`](https://huggingface.co/datasets/skeswa/swingset/tree/138352ef5e9ec25e12b60dc6acb85db808c5e77f), candidate `cand_80719e178655435d`, published 2026-09-21 10:26 UTC. Regression from `0923f27`: all published identity links, candidates, and placement registry points are absent ([receipt](../journal/evidence/quality/published-field-gaps-2026-09-21/receipt.json)).
- Previous baseline [`2a6c7dc`](https://huggingface.co/datasets/skeswa/swingset/tree/2a6c7dc744fb36eabb5163c0a527d787d3721f4f), the manual H16 release of 2026-09-16 ([release outcome](../journal/investigations/2026/h16-production-release-2026-09-16.md)).

## Running

- Worker: OrbStack NixOS machine `swingset`, candidate 017 at schema 32, publication off. Cycle, backup, and summary services and timers are stopped under the owner's pause; the all-scope pause is persisted ([pause receipt](../journal/evidence/runtime/worker-pause-2026-09-24/receipt.json), [D-0197](../journal/decisions/0197-pause-worker-for-failure-and-capacity-review.md)).
- Timers: cycle every 15 minutes with a 40-minute budget and `--dry-run` (D-0189); backup 04:00 UTC; summary 08:00 UTC; scratch clean 05:00 UTC ([deployment receipt](../journal/evidence/runtime/candidate-012-2026-09-21/receipt.json)).
- Collection: paused. The latest snapshot was fetched 2026-09-24 06:57 UTC; phase two has indexed archive captures but fetched no platform score-sheet body because `project/history/all` remains blocked. Links (2,932) wait behind the projection ([pause receipt](../journal/evidence/runtime/worker-pause-2026-09-24/receipt.json), [D-0195](../journal/decisions/0195-answer-the-projection-question-once-per-pass.md)).
- Outage: 2026-09-22 04:44 to 16:37 UTC, every cycle failed on a full disk after the backup retried into six 26 GB copies; four removed after an independent check, the unit no longer restarts, and a room check now refuses a backup the quota cannot hold ([receipt](../journal/evidence/runtime/disk-full-recovery-2026-09-22/receipt.json), [D-0192](../journal/decisions/0192-a-failed-backup-waits-for-the-next-timer-run.md)).
- Phase two opened 2026-09-23 11:58 UTC with the last acceptance; the next two cycles failed on the 45-second write deadline because dispatch re-evaluated all 6,342 plan pages inside one write; candidate 016 answers the projection question once per pass and evaluates only the allocated page (D-0195).
- Backups: remote checkpoints fail while the transport archive exceeds 20 GiB (live database 23.8 GB); the local copy under `checkpoints/` is the only backup until the transport is split ([queue](plans/README.md#maintenance-queue)).
- Build failures: candidate 011 records 17 failed cycles from stale link generations and makes build failure non-fatal so links can refresh; the 15:52 UTC quality measurement still found 1,063 pending link units ([deployment receipt](../journal/evidence/runtime/candidate-011-2026-09-21/receipt.json), [quality receipt](../journal/evidence/quality/published-field-gaps-2026-09-21/receipt.json)).
- Backlog: candidate 010 drained 19,202 units in its first cycle; the drain then crossed the 8 GB size cap and paused itself, so the cap is now 32 GB (D-0186). That config-only switch re-queued the parse units again (31,821 parses, 13,152 projections at 04:57 UTC), which is why switches are batched from here on ([receipt](../journal/evidence/runtime/candidate-010-2026-09-21/receipt.json)).

## Held

- Operator hold: codex, at the owner's request, for build failure, history projection, and capacity review; review by 2026-09-25. The active cycle exceeded systemd's stop window and was killed; its build admission still needs reconciliation before resume ([pause receipt](../journal/evidence/runtime/worker-pause-2026-09-24/receipt.json)).

## Next release and what blocks it

- Next release priority: [recover published identity output](plans/data-quality.md). Reproduced: the new history snapshot rejects all 178 old populated links; publication lacks a coverage-loss check, and the null baseline blocks identity restoration. Also reproduced: 435 old events withheld by source-acceptance checks, plus one intentional alias merge. Production controls are unchanged ([diagnosis receipt](../journal/evidence/quality/published-field-gaps-2026-09-21/regression-diagnosis.json)).
- Every Python change re-derives the whole graph (five hours, plus a graph of generations the collector cannot yet remove). The [derivation invalidation plan](plans/derivation-invalidation.md) removes both causes in about two weeks once phase two has run clean for a day; until its first switch lands, batch Python changes and prefer none. Config and override edits are safe.
- Local documentation adds the [quality investigation and recovery plan](plans/data-quality.md); this session deployed no repair. Step Right and DCN still lack an archive acquisition path ([plan](plans/history-and-recovery.md#event-completion-follow-up-in-v6)); CDX indexing runs each cycle (D-0184).
- Historical years: all seventeen, 2010 to 2026, accepted 2026-09-23 between 02:53 and 11:58 UTC by review agents and the session on the owner's direction; 89 phase-one findings and 936 alias findings were resolved through `series_aliases.csv` and `finding_reviews.csv`, two of the last dismissals on the weight of evidence rather than proof (D-0180, D-0187, D-0193; [receipt](../journal/evidence/runtime/v5-year-acceptance-2026-09-22/receipt.json), [review note](../journal/investigations/2026/undated-newsletter-listings-2026-09-22.md)).
- Global Grand Prix: an Opus review mapped December 2025 to `wsdc-342` and September 2026 to `wsdc-409`; three month-scoped aliases were accepted at 21:13 UTC. Both findings and the split 2026 event rows await the queued history projection ([receipt](../journal/evidence/runtime/global-grand-prix-aliases-2026-09-23/receipt.json)).

## Known limits

- Metadata: 92.0% of published cities/countries are null; 254 events have month-only dates. Heats have no producer ([investigation](../journal/investigations/2026/published-field-gaps-2026-09-21.md), [receipt](../journal/evidence/quality/published-field-gaps-2026-09-21/receipt.json)).
- Identity matching is best effort. Human review of the representative sample has not happened ([D-0055](../journal/decisions/0055-defer-human-adjudication-to-a-review-website.md)).
- Historical score-sheet coverage is incomplete; the [Jes Test](../journal/investigations/2026/jesann-history-page-2026-09-17.md) found individual results for 19 of 85 registry entries in the baseline.
- State size: the live database is 64.08 GB against a 64 GB cap; failed builds added about 471 MB per cycle. Worker quota had 91.3 GB free at 14:54 UTC. Local checkpoints occupy about 92 GB and are the only working backup; do not remove them without the required independent check ([pause receipt](../journal/evidence/runtime/worker-pause-2026-09-24/receipt.json), [size investigation](../journal/investigations/2026/derivation-invalidation-2026-09-23.md)).
- Candidate history: [what each frozen source changed](reference/candidate-history.md). Tests: core 989 cases in about three minutes, 2,039 more in the extended suite ([timing](../journal/evidence/runtime/core-test-suite-2026-09-18/receipt.json)).

Update a line when its receipt changes. Keep this page under 60 lines.
