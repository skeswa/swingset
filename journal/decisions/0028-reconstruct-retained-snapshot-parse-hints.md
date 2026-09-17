# D-0028: Reconstruct retained snapshot parse hints

Status: Proposed  
Recorded: 2026-09-16  
Accepted: —  
Acceptance source: The owner authorized continued history and recovery implementation. This local repair has not received operating acceptance.  
Topic: Queue recovery  
Supersedes: —  
Superseded by: —

## Decision

Reconstruct missing parse hints for pending successful snapshots on currently
declared event-member watches. Use retained snapshot, attempt, and same-unit
admission records. The parent being archived or sealed does not erase a child's
unfinished task. Queue reconstruction reads metadata only; ordinary work gates
still govern execution and artifact recovery.

Preserve the original attempt token when available, retry generation, retry
deadline, and terminal latch. Unchanged-input terminal attempts and same-unit
admission decisions do not become new work. Changed inputs or an explicit retry
may make the task eligible through the existing rules. A failed parse is
recoverable only when its explicit retry generation exceeds the latest own
attempt. Superseded or completed snapshots and failed transport records remain
outside this narrow repair.

## Bounds and authority

Scan at most 100 candidates per call with shared metadata and elapsed-time
bounds. Freeze a snapshot-row high-water for each pass so arrivals cannot
displace existing candidates. Stop the cursor at work actually assessed; retry
work denied enough remaining budget on a later pass. Malformed or oversized
metadata remains unassessed.

Recover the exact snapshot's parse task, not an event-stage absence claim.
Another unit's aggregate interpretation may use that snapshot without completing
its own parse task. Reconstructing a hint creates no successful-operation or
event-progress receipt. Undeclared normalized alias watches remain outside
this initial recovery scan.

## Validation

Exercise actual interpretation after deleting its queue hint, including archived
and sealed parents, pause/resume, retries, terminal decisions, changed inputs,
aggregate dependencies, bounded scans, and continuous arrivals. Record that
reconstruction makes no source requests, reads no artifacts, and leaves watch
state and attempt history intact. This code remains outside the frozen H16
preservation release.

## Links

- [Failure and recovery contract](../../docs/reference/scheduling.md#failure-pause-and-recovery).
- [Stage output and progress distinction](0026-record-stage-outputs-and-verified-event-progress.md).
