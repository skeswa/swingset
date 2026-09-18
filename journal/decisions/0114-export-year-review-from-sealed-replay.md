# D-0114: Export year review from the sealed replay

Recorded: 2026-09-17  
Decided by: agent  
Topic: Historical review  
Supersedes: —  
Superseded by: —

## Decision

Build the next 2010–2026 review from the completed parser-8 scratch with a
dedicated offline exporter. Bind it to the exact replay receipt, successor
ledger and all 117 table hashes. Copy the SQLite closure with WAL-aware SQLite
backup, open only that private copy read-only and write a fresh review directory
under `/var/tmp`.

The exporter must not accept a year, change production, fetch, run child
processes or publish. Retain one compact receipt with hashes instead of another
copy of the review bundle.

## Why

The older review predates the exact parser-8 replay. A bound export shows the
review impact without treating scratch interpretation as production or owner
acceptance, and the compact receipt follows the current evidence-volume rule.

This proposal records an implementation choice under D-0087. It does not grant
historical-year acceptance.

## Links

- [Phase-one replay decisions](0111-seal-phase-one-replay-before-execution.md)
- [Evidence-volume decision](0105-reduce-new-evidence-volume.md)
- [Phase-one resume review](../investigations/2026/v2-phase-one-resume-review-2026-09-17.md)
