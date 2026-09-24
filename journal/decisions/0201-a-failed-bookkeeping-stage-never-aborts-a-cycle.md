# D-0201: A failed bookkeeping stage never aborts a cycle

Recorded: 2026-09-25  
Decided by: agent, 2026-09-25, under D-0183  
Topic: Operations  
Supersedes: —  
Superseded by: —

## Decision

The stages a cycle runs before acquisition (event enumeration bootstrap, parse
hint recovery, event pressure bootstrap, blocker observations, the requirement
scan, the event-site review, and the pressure and progress refreshes) are
contained. A write-deadline overrun in any of them rolls back that stage only,
is listed in the cycle's `stage_errors`, and sets `failed=true`. The cycle
goes on to fetch and derive. The event-site review now answers the projection
question once inside its write, not once per site. This ships as candidate 019.

## Why

From 2026-09-25 01:32 UTC every cycle whose phase-two gate was open died about
55 seconds in, before acquisition, with `WriteDeadlineExceeded`. The event-site
review asked `phase_two_allowed` for each of up to ten sites inside one
45-second write, at about ten seconds per answer. Before D-0198 a pending
history kept the gate shut, so the review never reached that write. Only the
cycles right after a build, when a projection was pending, got through. One
optional stage stopping all collection breaks rule 1 of the stability plan.

## Links

- [Stability plan](../../docs/plans/stability.md)
