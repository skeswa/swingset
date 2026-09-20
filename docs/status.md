# Current status

Updated 2026-09-20 23:40 UTC. Five sections; each line links to one receipt.
History lives in the [journal](../journal/README.md); the last long-form page
is [archived](../journal/archive/status-2026-09-18.md).

## Published

- Public dataset [`2a6c7dc`](https://huggingface.co/datasets/skeswa/swingset/tree/2a6c7dc744fb36eabb5163c0a527d787d3721f4f), candidate `cand_8f31cad7226643ae`, published 2026-09-16 22:37 UTC ([release outcome](../journal/investigations/2026/h16-production-release-2026-09-16.md)).
- Automatic publication resumed 2026-09-20 23:09 UTC when the hold was lifted ([receipt](../journal/evidence/runtime/collection-resume-2026-09-20/receipt.json)). No release has been published since; the first one follows the backlog drain below.

## Running

- Worker: OrbStack NixOS machine `swingset`, candidate 006 at schema 29, source `/nix/store/rgyryll4d55rgzscdqhjcwmkr325a76f-source` ([migration audit](../journal/evidence/runtime/held-schema29-migration-2026-09-17/postmigration-review-001/receipt.json)).
- Timers: cycle every 15 minutes with a 12-minute budget and `--publish`; backup 04:00 UTC; summary 08:00 UTC; scratch clean 05:00 UTC.
- First post-hold cycle: started 23:09:58 UTC and still parsing at 23:40 UTC; outcome in the [receipt](../journal/evidence/runtime/collection-resume-2026-09-20/receipt.json).
- Backlog at resume: 31,821 queued parse units and one projection, no application pauses (pre-resume doctor digest in the same receipt). Cycles parse that backlog before new fetches are admitted.

## Held

- No operator hold. An overdue hold, older than 48 hours, goes here first ([holds](guides/operation.md#holds)).

## Next release and what blocks it

- Next: the first automatic candidate after the parse backlog drains. Throughput is the only blocker; watch `journalctl -u swingset-cycle` for `event="cycle"` lines with `candidate_id`.
- Undeployed local work, each needing a candidate 007 freeze and switch under the production tier in [AGENTS.md](../AGENTS.md): Step Right admission and projection ([rehearsal](../journal/evidence/admission/stepright-admission-rehearsal-2026-09-18/receipt.json)); the restore-activation fix ([investigation](../journal/investigations/2026/restore-activation-admission-recovery-2026-09-18.md)); schemas 30 to 32 from the parked [bounded state plan](plans/bounded-state-and-archive.md).
- Historical years: none accepted; 17 calendar captures pending ([year review](../journal/investigations/2026/history-review-2026-09-17.md)).

## Known limits

- Identity matching is best effort. Human review of the representative sample has not happened ([D-0055](../journal/decisions/0055-defer-human-adjudication-to-a-review-website.md)).
- Historical score-sheet coverage is incomplete; the [Jes Test](../journal/investigations/2026/jesann-history-page-2026-09-17.md) found individual results for 19 of 85 registry entries in the baseline.
- Candidate history: [what each frozen source changed](reference/candidate-history.md). Tests: core 989 cases in about three minutes, 2,039 more in the extended suite ([timing](../journal/evidence/runtime/core-test-suite-2026-09-18/receipt.json)).

Update a line when its receipt changes. Keep this page under 60 lines.
