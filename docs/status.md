# Current status

Updated 2026-09-25 16:35 UTC. Five sections; each line links to one receipt.
History lives in the [journal](../journal/README.md); the last long-form page
is [archived](../journal/archive/status-2026-09-18.md).

## Published

- Worker-acknowledged public dataset [`138352e`](https://huggingface.co/datasets/skeswa/swingset/tree/138352ef5e9ec25e12b60dc6acb85db808c5e77f), candidate `cand_80719e178655435d`, published 2026-09-21 10:26 UTC. Regression from `0923f27`: all published identity links, candidates, and placement registry points are absent ([receipt](../journal/evidence/quality/published-field-gaps-2026-09-21/receipt.json)).
- Previous baseline [`2a6c7dc`](https://huggingface.co/datasets/skeswa/swingset/tree/2a6c7dc744fb36eabb5163c0a527d787d3721f4f), the manual H16 release of 2026-09-16 ([release outcome](../journal/investigations/2026/h16-production-release-2026-09-16.md)).

## Running

- Worker: OrbStack NixOS machine `swingset`, candidate 019 at schema 32, publication off, switched 2026-09-25 16:27 UTC. Candidate 018 finished its re-derivation (parse backlog 33,798 to the 610 parked units), settled history at 01:11 UTC and completed three builds, but from 01:32 UTC most cycles died at 55 s in the event-site review; 019 contains that (D-0201) and pays one more re-derivation ([receipt](../journal/evidence/runtime/candidate-019-2026-09-25/receipt.json)).
- Timers: cycle every 15 minutes with a 40-minute budget and `--dry-run` (D-0189); a build at most every 6 hours and a history projection at most every 12 (D-0199); backup 04:00 UTC; summary 08:00 UTC; scratch clean 05:00 UTC ([receipt](../journal/evidence/runtime/candidate-018-2026-09-24/receipt.json)).
- Collection: phase two is fetching. Since the 018 resume: 21 Wayback platform score-sheet snapshots (latest 2026-09-25 12:26 UTC) and origin requests to eepro.com (260), scoring.dance (121), points.worldsdc.com (120) and web.archive.org (26), all in the few cycles that survived the event-site overrun ([receipt](../journal/evidence/runtime/candidate-019-2026-09-25/receipt.json)).
- Outage: 2026-09-22 04:44 to 16:37 UTC, every cycle failed on a full disk after the backup retried into six 26 GB copies; four removed after an independent check, the unit no longer restarts, and a room check now refuses a backup the quota cannot hold ([receipt](../journal/evidence/runtime/disk-full-recovery-2026-09-22/receipt.json), [D-0192](../journal/decisions/0192-a-failed-backup-waits-for-the-next-timer-run.md)).
- Stall, 2026-09-21 16:34 to 2026-09-24: every build failed on one Budafest 2020 callback mark (100 cycles, 96 reporting `failed=false`), each failure kept a 235 MB closure proof, and history overran its write bound and closed phase two. Candidate 018 withholds such an event, keeps nothing on failure, counts build errors as failures and retries overruns ([investigation](../journal/investigations/2026/build-failure-and-history-deadline-2026-09-24.md)).
- Backups: the 04:00 UTC run wrote a 36 GB local checkpoint and failed the 20 GiB upload; with no successful upload nothing is pruned, and the room check now refuses new backups (83 GB free, 119 GB needed). Local checkpoints hold 130 GB; the 2026-09-24 byte copy on the Mac is verified. Pruning old checkpoints needs an independent check ([receipt](../journal/evidence/runtime/candidate-019-2026-09-25/receipt.json)).

## Held

- None. A diagnostic hold (claude) ran 16:10 to 16:27 UTC on 2026-09-25 for the candidate 019 switch ([receipt](../journal/evidence/runtime/candidate-019-2026-09-25/receipt.json)).

## Next release and what blocks it

- Plan of record: the [target architecture](plans/architecture.md), adopted 2026-09-25; all other plan work is paused until its phases land. Two owner decisions are open: year acceptance as a publication filter, and removing the `event_*` subsystem.
- Next release priority: [recover published identity output](plans/data-quality.md). Reproduced: the new history snapshot rejects all 178 old populated links; publication lacks a coverage-loss check, and the null baseline blocks identity restoration. Also reproduced: 435 old events withheld by source-acceptance checks, plus one intentional alias merge. Production controls are unchanged ([diagnosis receipt](../journal/evidence/quality/published-field-gaps-2026-09-21/regression-diagnosis.json)).
- Every Python change still re-derives the whole graph; candidate 018 is this week's one such switch. The [derivation invalidation plan](plans/derivation-invalidation.md) removes that cost; batch Python changes until it lands.
- Local documentation adds the [quality investigation and recovery plan](plans/data-quality.md); this session deployed no repair. Step Right and DCN still lack an archive acquisition path ([plan](plans/history-and-recovery.md#event-completion-follow-up-in-v6)); CDX indexing runs each cycle (D-0184).
- Historical years: all seventeen, 2010 to 2026, accepted 2026-09-23 between 02:53 and 11:58 UTC by review agents and the session on the owner's direction; 89 phase-one findings and 936 alias findings were resolved through `series_aliases.csv` and `finding_reviews.csv`, two of the last dismissals on the weight of evidence rather than proof (D-0180, D-0187, D-0193; [receipt](../journal/evidence/runtime/v5-year-acceptance-2026-09-22/receipt.json), [review note](../journal/investigations/2026/undated-newsletter-listings-2026-09-22.md)).
- Global Grand Prix: an Opus review mapped December 2025 to `wsdc-342` and September 2026 to `wsdc-409`; three month-scoped aliases were accepted at 21:13 UTC. Both findings and the split 2026 event rows await the queued history projection ([receipt](../journal/evidence/runtime/global-grand-prix-aliases-2026-09-23/receipt.json)).

## Known limits

- Metadata: 92.0% of published cities/countries are null; 254 events have month-only dates. Heats have no producer ([investigation](../journal/investigations/2026/published-field-gaps-2026-09-21.md), [receipt](../journal/evidence/quality/published-field-gaps-2026-09-21/receipt.json)).
- Identity matching is best effort. Human review of the representative sample has not happened ([D-0055](../journal/decisions/0055-defer-human-adjudication-to-a-review-website.md)).
- Historical score-sheet coverage is incomplete; the [Jes Test](../journal/investigations/2026/jesann-history-page-2026-09-17.md) found individual results for 19 of 85 registry entries in the baseline.
- State size: 35.2 GB (29.41 GB after the 2026-09-24 collection, then the re-derivation, three builds and links); quota 46.5 GB free, most of it held by local checkpoints ([receipt](../journal/evidence/runtime/candidate-019-2026-09-25/receipt.json)).
- Candidate history: [what each frozen source changed](reference/candidate-history.md). Tests: full suite 2,467 passed, 0 failed on 2026-09-24 ([receipt](../journal/evidence/runtime/candidate-018-2026-09-24/receipt.json)).

Update a line when its receipt changes. Keep this page under 60 lines.
