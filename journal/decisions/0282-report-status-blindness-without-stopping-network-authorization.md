# D-0282: Report status blindness without stopping network authorization

Recorded: 2026-11-10  
Decided by: agent, 2026-11-10; source: D-0172 and the stage 2 implementation request  
Topic: Namespace guard failures  
Supersedes: —  
Superseded by: —

## Decision

A failed peer-status observation publishes a fresh incomplete snapshot and a
monitoring failure, then lets the namespace guard continue authorization.
The watchdog must report that blindness. No old successful snapshot is reused.
Authorization, firewall and status-publication failures still stop the guard;
the utility supervisor still stops its owned job when its guard fails.

## Why

During stage 2, a Docker status request for a completed helper times out after
30 seconds and terminates the guard. Collecting that report does not install
or verify a worker's firewall. Keeping these failures separate preserves
authorization while making the missing observation explicit.

## Alternatives and consequences

Stopping on every status error also stops correctly fenced work for a reporting
failure. Continuing cannot claim healthy monitoring: incomplete inventory and
observer failure remain alerts. A failure to publish those alerts stays fatal.

## Links

[Measured failure](../evidence/runtime/postgres-stage2-2026-11-10/graph-supervisor-timeout-boundary.json).
