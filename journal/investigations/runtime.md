# Worker state, controls, and scheduling

Find investigations of unfinished work, retries, pause controls, saved outputs, and scheduling.

[All investigations](README.md) · [Current status](../../docs/status.md)

- [Pytest value and reduction review](2026/pytest-suite-review-2026-09-18.md) — 2026-09-18; all 217 test files reviewed, with conditional reductions and a separate count for tool tests. No tests removed.
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
- [Measuring where state storage goes](2026/state-storage-measurement-2026-09-18.md) — 2026-09-18; a read-only tool for per-table, per-stage and payload-digest numbers, and its run on a scratch copy of held checkpoint 004: rows do not repeat yet, derivation rows and their indexes are 27% of the file, and the interning migration (schema 32 since [D-0167](../decisions/0167-intern-derivation-payloads-in-the-last-migration.md)) makes the file 6% larger on this copy.
- [Worker disk exhaustion and its bound](2026/worker-disk-exhaustion-2026-09-18.md) — 2026-09-18; rehearsal scratch and old checkpoints removed, 256 GiB machine bound, scratch-clean timer deployed as a pinned hotfix.
- [Disk leak from `nix develop` and pytest](2026/nix-develop-tmpdir-leak-2026-09-18.md) — 2026-09-18; leaked TMPDIR directories filled the Mac; the dev shell now pins TMPDIR so pytest prunes.
