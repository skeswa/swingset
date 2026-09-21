# D-0197: Pause the worker for failure and capacity review

Recorded: 2026-09-24  
Decided by: owner, 2026-09-24; source: “please pause the worker too”  
Topic: Operations  
Supersedes: —  
Superseded by: —

## Decision

Hold the worker's cycle, backup, and summary services while the repeated build
failure, blocked history projection, and database growth are diagnosed. Keep an
all-scope operator pause as a second interlock. Review the hold by 2026-09-25;
resume only after the capacity and interrupted-build state are checked.

## Why

Failed release builds added about 471 MB per cycle without fetching score
sheets or advancing the blocked projection. The database reached 64.08 GB,
above its 64 GB safety cap. The owner requested a pause before handing off
diagnosis.

## Links

- [Operation receipt](../evidence/runtime/worker-pause-2026-09-24/receipt.json)
