# D-0235: Build weekly until phase 4 frees room

Recorded: 2026-10-03  
Decided by: owner, 2026-10-03; source: "Build weekly (Recommended)"  
Topic: Scheduling  
Supersedes: the 24-hour `build_interval_seconds` of [D-0220](0220-raise-the-cap-to-72-gb-for-the-phase-1-switch-only.md), in production  
Superseded by: [D-0245](0245-cap-the-database-at-50-gb-and-build-daily-after-the-reclaim.md)

## Decision

Production sets `build_interval_seconds` to 604,800 (7 days) until phase
4's storage work is deployed; that switch sets its own value. The last
build was 2026-10-03 04:24 UTC, so the next is about 2026-10-10.

## Why

Each build adds about 1.3 GB that nothing collects until phase 4: the first
build under phase 2 took the file from 71.34 to 72.66 GB and over the 72 GB
cap (D-0233). At one build a day the 76 GB cap lasts about two days.
Publication is off, so builds only make dry-run candidates, and switch 2b
already has its build measurement. A scheduling setting re-queues nothing.

## Links

- [Receipt](../evidence/runtime/architecture-phase2a-2026-10-02/receipt.json)
