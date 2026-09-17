# D-0046: Separate phase-one acquisition from review gaps

Status: Proposed  
Recorded: 2026-09-17  
Accepted: —  
Acceptance source: —  
Topic: Historical event review  
Supersedes: —  
Superseded by: —

## Decision

Prepare year review packets from hashed, retained exports. Show pending capture
requests separately from acquired bodies with interpretation warnings. Keep the
original findings, target identities and recorded acceptance flags. Generate a
new directory for each report. Use the exact deployed runtime and a read-only
writer lock when preparing a finite phase-one resume gate.

## Why

The fresh production export has 17 pending requests, but 2026 lists 56 capture
gaps: 17 pending targets, four acquired map bodies that failed interpretation,
and 35 parsed captures with warnings. Counting all 56 as missing bodies would
repeat acquisition without resolving the evidence. No year can be accepted
from these counts alone.

## Links

- [Dated reconciliation](../investigations/2026/history-review-2026-09-17.md).
- [Backfill acceptance contract](../../docs/reference/backfill.md#events-first).
