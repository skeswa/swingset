# Current status

Updated 2026-10-01 21:45 UTC. Five sections; each line links to one receipt.
History lives in the [journal](../journal/README.md); the last long-form page
is [archived](../journal/archive/status-2026-09-18.md).

## Published

- Worker-acknowledged public dataset [`138352e`](https://huggingface.co/datasets/skeswa/swingset/tree/138352ef5e9ec25e12b60dc6acb85db808c5e77f), candidate `cand_80719e178655435d`, published 2026-09-21 10:26 UTC. Regression from `0923f27`: all published identity links, candidates, and placement registry points are absent ([receipt](../journal/evidence/quality/published-field-gaps-2026-09-21/receipt.json)).
- Previous baseline [`2a6c7dc`](https://huggingface.co/datasets/skeswa/swingset/tree/2a6c7dc744fb36eabb5163c0a527d787d3721f4f), the manual H16 release of 2026-09-16 ([release outcome](../journal/investigations/2026/h16-production-release-2026-09-16.md)).

## Running

- Worker: OrbStack NixOS machine `swingset`, architecture phase 1 (output cutoff, scoped recipes, schema 33) with phase 0b stage switches, switched 2026-09-29 04:59 UTC after an independent check; publication off. The re-derivation drained in five hours with no failed cycle: about 67,000 equivalences instead of new generations, and the file grew 0.3 GB ([receipt](../journal/evidence/runtime/architecture-phase1-2026-09-29/receipt.json)).
- Timers: cycle every 15 minutes with a 40-minute budget and `--dry-run` (D-0189); a build at most every 6 hours and a history projection at most every 12 (D-0199); backup 04:00 UTC; summary 08:00 UTC; scratch clean 05:00 UTC ([receipt](../journal/evidence/runtime/candidate-018-2026-09-24/receipt.json)).
- Collection: no page was requested from 2026-09-29 20:28 UTC: the offers pass re-asked the projection question for every origin-fallback page and never reached a fetch. Hotfix deployed 2026-10-01 21:37 UTC; the Mac also slept 21 hours on battery (maintenance item 12). 104 Wayback platform score sheets so far ([receipt](../journal/evidence/runtime/offers-cache-hotfix-2026-10-01/receipt.json)).
- Outage: 2026-09-22 04:44 to 16:37 UTC, every cycle failed on a full disk after the backup retried into six 26 GB copies; four removed after an independent check, the unit no longer restarts, and a room check now refuses a backup the quota cannot hold ([receipt](../journal/evidence/runtime/disk-full-recovery-2026-09-22/receipt.json), [D-0192](../journal/decisions/0192-a-failed-backup-waits-for-the-next-timer-run.md)).
- Stall, 2026-09-21 16:34 to 2026-09-24: every build failed on one Budafest 2020 callback mark (100 cycles, 96 reporting `failed=false`), each failure kept a 235 MB closure proof, and history overran its write bound and closed phase two. Candidate 018 withholds such an event, keeps nothing on failure, counts build errors as failures and retries overruns ([investigation](../journal/investigations/2026/build-failure-and-history-deadline-2026-09-24.md)).
- Backups: local only (D-0203), but the room check refuses them until quota is freed (needs about 98 GB, 90.5 GB free); the first backup verify under phase 1 is deferred. Verified Mac byte copies: 2026-09-29 (sha256 `4784c742…`, integrity_check ok) and 2026-09-26 ([receipt](../journal/evidence/runtime/architecture-phase1-2026-09-29/receipt.json)).

## Held

- None. A diagnostic hold (claude) ran 16:10 to 16:27 UTC on 2026-09-25 for the candidate 019 switch ([receipt](../journal/evidence/runtime/candidate-019-2026-09-25/receipt.json)).

## Next release and what blocks it

- Plan of record: the [target architecture](plans/architecture.md), adopted 2026-09-25; all other plan work is paused. Phases 0 and 1 are deployed; phase 2 is implemented offline; the implementation workflow is stopped for the phase 1 drain and resumes after its post-check.
- Next release priority: [recover published identity output](plans/data-quality.md). Reproduced: the new history snapshot rejects all 178 old populated links; publication lacks a coverage-loss check, and the null baseline blocks identity restoration. Also reproduced: 435 old events withheld by source-acceptance checks, plus one intentional alias merge. Production controls are unchanged ([diagnosis receipt](../journal/evidence/quality/published-field-gaps-2026-09-21/regression-diagnosis.json)).
- Every Python change still re-derives the whole graph; candidate 018 is this week's one such switch. The [derivation invalidation plan](plans/derivation-invalidation.md) removes that cost; batch Python changes until it lands.
- Local documentation adds the [quality investigation and recovery plan](plans/data-quality.md); this session deployed no repair. Step Right and DCN still lack an archive acquisition path ([plan](plans/history-and-recovery.md#event-completion-follow-up-in-v6)); CDX indexing runs each cycle (D-0184).
- Historical years: all seventeen, 2010 to 2026, accepted 2026-09-23 between 02:53 and 11:58 UTC by review agents and the session on the owner's direction; 89 phase-one findings and 936 alias findings were resolved through `series_aliases.csv` and `finding_reviews.csv`, two of the last dismissals on the weight of evidence rather than proof (D-0180, D-0187, D-0193; [receipt](../journal/evidence/runtime/v5-year-acceptance-2026-09-22/receipt.json), [review note](../journal/investigations/2026/undated-newsletter-listings-2026-09-22.md)).
- Global Grand Prix: an Opus review mapped December 2025 to `wsdc-342` and September 2026 to `wsdc-409`; three month-scoped aliases were accepted at 21:13 UTC. Both findings and the split 2026 event rows await the queued history projection ([receipt](../journal/evidence/runtime/global-grand-prix-aliases-2026-09-23/receipt.json)).

## Known limits

- Metadata: 92.0% of published cities/countries are null; 254 events have month-only dates. Heats have no producer ([investigation](../journal/investigations/2026/published-field-gaps-2026-09-21.md), [receipt](../journal/evidence/quality/published-field-gaps-2026-09-21/receipt.json)).
- Identity matching is best effort. Human review of the representative sample has not happened ([D-0055](../journal/decisions/0055-defer-human-adjudication-to-a-review-website.md)).
- Historical score-sheet coverage is incomplete; the [Jes Test](../journal/investigations/2026/jesann-history-page-2026-09-17.md) found individual results for 19 of 85 registry entries in the baseline.
- State size: 70.99 GB against the 72 GB cap (D-0220) on 2026-10-02 04:25 UTC. Each inventory refresh re-mints every event and link, about 1.4 GB, which phase 1 correctly cannot cut off. D-0228 paces the inventory weekly (next about 2026-10-09 00:05) and D-0227 keeps history at 48 hours until phase 3; if phase 3 has not deployed by 10-09, raise the cap to about 76 GB. Phase 4 is what shrinks the file ([receipt](../journal/evidence/runtime/weekly-inventory-pacing-2026-10-02/receipt.json)).
- Candidate history: [what each frozen source changed](reference/candidate-history.md). Tests: full suite 2,467 passed, 0 failed on 2026-09-24 ([receipt](../journal/evidence/runtime/candidate-018-2026-09-24/receipt.json)).

Update a line when its receipt changes. Keep this page under 60 lines.
