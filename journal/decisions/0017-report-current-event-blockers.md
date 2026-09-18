# D-0017: Report current event blockers without inferring eligibility

Recorded: 2026-09-16  
Decided by: agent  
Topic: Event completion reporting  
Supersedes: —  
Superseded by: —

## Decision and reason

The doctor event drill-down will show recorded watch timing, applicable pauses,
source configuration, host cooldown, daily usage and limits, and parsing or work
backpressure from its existing read snapshot. Use the actual request host,
including an archive host, and keep overlapping blockers visible. Reading the
report must not acquire a request grant, expire controls, or change a watch.

These facts explain common reasons for waiting. They are an incomplete gate
assessment: historical dispatch, capture, robots, request-chain limits and other
checks still determine whether a request may issue. Do not emit a true
eligibility flag from the absence of these blockers. Current facts also cannot
reconstruct past eligible age or replace durable blocker transitions.
