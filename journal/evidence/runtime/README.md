# Worker state and recovery evidence

Check migrations, controls, scheduling, checkpoints, and isolated replay.

[All evidence](../README.md) · [Investigations](../../investigations/runtime.md)

| Bundle                                                                       | What it supports                                                                                                                               |
| ---------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------- |
| [h11](h11/)                                                                  | [Checking the missing-work inventory (H11)](../../investigations/2026/h11-acceptance-2026-09-13.md)                                            |
| [h12](h12/)                                                                  | [Checking isolated work and retries (H12)](../../investigations/2026/h12-acceptance-2026-09-13.md)                                             |
| [h13](h13/)                                                                  | [Checking pause controls and status reports (H13)](../../investigations/2026/h13-acceptance-2026-09-13.md)                                     |
| [h14](h14/)                                                                  | [Checking fair scheduling (H14)](../../investigations/2026/h14-acceptance-2026-09-13.md)                                                       |
| [h15](h15/)                                                                  | [Checking saved outputs and their inputs (H15)](../../investigations/2026/h15-acceptance-2026-09-13.md)                                        |
| [Event completion, 2026-09-16](event-completion-2026-09-16/)                 | [Retained event pages and bounded turns](../../investigations/2026/event-completion-2026-09-16.md)                                             |
| [Normalized request lookup, 2026-09-16](normalized-request-lookup-20260916/) | [Candidate-limit reproduction and proposed lookup options](../../investigations/2026/normalized-request-lookup-2026-09-16.md); not implemented |
| [Extension rollout, 2026-09-17](event-extension-2026-09-17/) | [Current held schema-28 handoff](../../investigations/2026/event-extension-operating-handoff-2026-09-17.md), frozen validation, deployment, migration and restore receipts |
| [Input rehearsal, 2026-09-17](extension-input-rehearsal-2026-09-17/) | Disposable checkpoint copy, exact input acceptance and bounded offline turns; no live acceptance |
| [Legacy spacing, 2026-09-17](legacy-spacing-baseline-2026-09-17/) | Reviewed Archive baseline preparation and guarded application; six other hosts remain unknown |

These are dated captures, reports, and frozen scripts. Their internal paths
refer to the original checkout. See the [path map](../paths.json) for relocated files.
