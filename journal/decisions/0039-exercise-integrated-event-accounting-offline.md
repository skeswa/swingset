# D-0039: Exercise integrated event accounting offline

Recorded: 2026-09-16  
Decided by: agent  
Topic: Event completion validation  
Supersedes: —  
Superseded by: —

## Decision

Use [the cohort fixture](../../tests/test_event_completion_cohort.py) to combine
admitted enumerations, ordinary scheduler selection, mocked HTTP through the real
fetch path, gated parsing and admission, and artifact-backed accounting. Keep a
dated current event due while new indexes arrive. Exercise the configured 40:30
new-work/current-work weights, actual pause controls, and backlog watermark
reopening without deleting watches or manually sealing target pages.

The fixture permits 600 selections, 601 requests, and a 128-request service gap,
with a 10,000-request daily cap. These are test-only bounds that keep one finite
experiment reproducible; they are neither production defaults nor measured
service guarantees.

Use [the activated restore fixture](../../tests/test_event_accounting_restore.py)
to create and activate a real disposable schema 24 checkpoint, then compare
fresh accounting and subsequent fetch/parse progress with an uninterrupted copy.
Preserve historical receipts and invalidate sampled observations through the
activation epoch. Include rejected support, changed response metadata, and lost
and restored bodies.

## Why and limits

The integrated fixtures close a gap left by tests of individual components:
fair turns must actually reach admitted artifacts and local accounting, while
restore must preserve history without reviving stale completion. Mocked HTTP and
a fake remote head keep these checks offline and repeatable.

The restore test covers current schema 24 state introduced across schemas 21–24;
it does not test historical schema migration. Neither fixture proves whole-cycle,
linking, build, publication, production recovery, ordinary-host throughput, or
continuous eligible waiting. Keep those acceptance claims separate.

## Links

- [Cohort investigation and exact results](../investigations/2026/event-completion-cohort-2026-09-16.md)
- [Activated accounting investigation and exact limits](../investigations/2026/event-accounting-activated-restore-2026-09-16.md)
- [Independent formatted-file validation receipt](../evidence/runtime/event-completion-2026-09-16/new-acceptance-validation-001.json)
