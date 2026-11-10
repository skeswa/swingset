# D-0283: Use separate Healthchecks.io alert and liveness checks

Recorded: 2026-11-10  
Decided by: owner, 2026-11-10; source: the stage 2 Healthchecks.io selection and confirmed test emails  
Topic: Rehearsal monitoring  
Supersedes: —  
Superseded by: —

## Decision

Use a dedicated Healthchecks.io project with email notifications for the server
rehearsal. Keep two checks: explicit owner alerts use a failure endpoint, while
only the health watchdog sends the liveness heartbeat. A successful heartbeat
cannot clear an owner alert.

Mount only the two private ping URLs in application services. Keep the
project management API key in the private server configuration. Add only
`https://hc-ping.com` to the application proxy's external origins; keep the
worker resolver restricted to internal names.

## Why

The owner has no Slack account and chooses Healthchecks.io after initially
excluding it. Both provider test emails arrive. Separate checks preserve an
explicit failure while allowing the watchdog to report that it is still alive.
Guarded application delivery still needs its own rehearsal proof.

## Links

[Monitoring proofs](../investigations/2026/stage-2-server-rehearsal-2026-11-05.md).
