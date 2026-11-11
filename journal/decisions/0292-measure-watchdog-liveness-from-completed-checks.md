# D-0292: Measure watchdog liveness from completed checks

Recorded: 2026-11-11  
Decided by: agent, 2026-11-11; source: authorized stage 2 monitoring repair  
Topic: Watchdog clocks and cadence  
Supersedes: —  
Superseded by: —

## Decision

Timestamp live watchdog reports after checks and notification delivery.
Compare platform status with the time it is observed. Pace the watchdog
from each cycle's monotonic start, waiting only the remainder of its
300-second interval. An overrun starts one following cycle without
replaying missed starts. Keep the 390-second liveness limit, resource caps
and separate owner-alert and dead-man checks. Explicit historical clocks
remain fixed for deterministic checks.

## Why

The rehearsal's checks take about 102 seconds. A platform snapshot
published during those checks can appear to be in the future when compared
with cycle start. Dating the heartbeat at that start and sleeping another
300 seconds after work also makes a working watchdog look stale. Offline
regressions reproduce both errors. Completion timestamps and an interval
that includes work preserve the existing liveness contract; they do not
establish normal cadence or make queries faster.

## Links

[Health and alerts](../../docs/plans/postgres-migration/4-hosting.md#health-and-alerts).
