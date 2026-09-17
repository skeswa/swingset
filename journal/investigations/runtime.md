# Worker state, controls, and scheduling

Find investigations of unfinished work, retries, pause controls, saved outputs, and scheduling.

[All investigations](README.md) · [Current status](../../docs/status.md)

- [Event accounting after checkpoint activation](2026/event-accounting-activated-restore-2026-09-16.md) — 2026-09-16; actual schema 24 activation and resumed accounting, with rejected and changed support controls.
- [Normalized request lookup](2026/normalized-request-lookup-2026-09-16.md) — 2026-09-16; reproduced candidate crowding and proposed portable index options, not implemented.
- [Proving explicit page retirement](2026/event-page-retirement-proof-2026-09-16.md) — 2026-09-16; bounded immediate-edge proof, whole-event retirement unknown.
- [Observed event completion and fleet accounting](2026/event-fleet-accounting-2026-09-16.md) — 2026-09-16; first bounded local increment, retirement deferred.
- [Retained event pages and bounded turns](2026/event-completion-2026-09-16.md) — 2026-09-16.
- [Preparing bounded event-index expansion](2026/event-expansion-watermark-2026-09-16.md) — 2026-09-16; proposed design, not implemented.
- [Checking the missing-work inventory (H11)](2026/h11-acceptance-2026-09-13.md) — 2026-09-13.
- [Preparing the missing-work inventory deployment (H11)](undated/h11-deployment-preparation.md) — date and scope in record.
- [Measuring the missing-work inventory (H11)](2026/h11-retained-inventory-2026-09-13.md) — 2026-09-13.
- [Checking isolated work and retries (H12)](2026/h12-acceptance-2026-09-13.md) — 2026-09-13.
- [Preparing work-isolation migration checks (H12)](undated/h12-deployment-preparation.md) — date and scope in record.
- [Checking that backups include saved output files (H12)](2026/h12-generation-closure-2026-09-13.md) — 2026-09-13.
- [Checking pause controls and status reports (H13)](2026/h13-acceptance-2026-09-13.md) — 2026-09-13.
- [Finding files inside nested checkpoint manifests (H13)](2026/h13-checkpoint-closure-fix-2026-09-13.md) — 2026-09-13.
- [Preparing pause-control migration checks (H13)](undated/h13-deployment-preparation.md) — date and scope in record.
- [Checking fair scheduling (H14)](2026/h14-acceptance-2026-09-13.md) — 2026-09-13.
- [Preparing scheduler migration checks (H14)](undated/h14-deployment-preparation.md) — date and scope in record.
- [Measuring scheduled work without activating it (H14)](2026/h14-shadow-load-2026-09-13.md) — 2026-09-13.
- [Checking saved outputs and their inputs (H15)](2026/h15-acceptance-2026-09-13.md) — 2026-09-13.
- [Measuring selection of local work (H15)](2026/h15-offline-selection-profile-2026-09-13.md) — 2026-09-13.
- [Current held extension runtime and operating gates](2026/event-extension-operating-handoff-2026-09-17.md) — 2026-09-17.
- [Measured offline selector costs and bounded fixes](2026/offline-selector-profile-2026-09-17.md) — 2026-09-17.
- [Local historical dispatch timing proofs](2026/historical-archive-timing-2026-09-17.md) — 2026-09-17.
