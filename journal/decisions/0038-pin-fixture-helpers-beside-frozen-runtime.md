# D-0038: Pin fixture helpers beside the frozen runtime

Status: Proposed  
Recorded: 2026-09-16  
Accepted: —  
Acceptance source: Coordinator accepted engineering preparation after independent review; owner fixture-acquisition approval remains pending.  
Topic: Fixture acquisition preparation  
Supersedes: —  
Superseded by: —

## Decision

Prepare a new version of the existing H13 fixture driver for the exact deployed
schema-14 source. Supply its missing research helpers and retained allowlist
evidence in a separately hashed closure. Verify every helper file before import;
reject symlinks and undeclared files, including bytecode and extensions. Keep all
runtime imports and the database schema bound to the frozen source.

## Why

The old driver pins helper paths that moved during documentation reorganization.
The deployed source omits those helpers. Importing the current development tree
would mix schema 23 with production schema 14. A small explicit helper closure
preserves the existing downloader and its exact request limits without a runtime
migration or weakened source check.

## Consequences

The original evidence remains unchanged. The new driver retains H13 admission,
pause draining, writer ownership, restricted writes, robots, request/byte budgets,
and the finite allowlist. It requires the scheduled-service hold to remain present
and creates private outputs. The local and independent runs each passed 63 offline
tests against the verified frozen mirror. Engineering review does not authorize
staging, capture, parser activation, year acceptance, or publication. Actual owner
approval, current execution pins, quota, and nonoverlapping ownership remain required.

## Links

- [Parser and readiness investigation](../investigations/2026/historical-parser-evidence-gaps-2026-09-16.md)
- [Preserved review packet](../evidence/admission/fixture-exception-2026-09-16/REVIEW.md)
- [Independent coordinator receipt](../evidence/admission/fixture-exception-2026-09-16/root-review-receipt-001.json)
- [Exact acquisition proposal](../investigations/2026/v2-new-source-fixture-proposal-2026-09-13.md)
