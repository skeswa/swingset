# D-0124: Continue v2 acquisition beyond prior request allowances

Status: Accepted  
Recorded: 2026-09-17  
Accepted: 2026-09-17, project owner  
Acceptance source: Session instruction: “all scraping limits have been waived until the conclusion of v2 impl - feel free to keep trying”  
Topic: V2 acquisition operations  
Supersedes: —  
Superseded by: —

## Decision

Do not stop necessary v2 acquisition solely because an earlier request, byte or
retry allowance was exhausted. Continue bounded attempts until v2
implementation concludes. Retain exact request accounting, robots handling,
single-host concurrency, source/config controls, operator holds and failure
evidence.

## Why

Earlier fixture and phase operations used narrow per-operation allowances. The
owner explicitly waived those scrape limits so transient failures or exhausted
allowances do not leave v2 implementation incomplete. Durable accounting and
runtime controls are still needed to distinguish attempts from successful
acquisition and to preserve recovery evidence.

This authority does not accept a historical year, enable an ordinary source,
remove the production hold or publish data.

## Links

- [D-0087](0087-authorize-remaining-v2-acquisition-and-operations.md)
- [Fetching reference](../../docs/reference/fetching.md)
- [History and recovery plan](../../docs/plans/history-and-recovery.md)
