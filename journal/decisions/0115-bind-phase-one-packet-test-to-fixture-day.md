# D-0115: Bind the phase-one packet test to its fixture day

Recorded: 2026-09-17  
Decided by: agent  
Topic: Validation  
Supersedes: —  
Superseded by: —

## Decision

Make the end-to-end phase-one packet preflight test use the same fixed UTC day
as its disposable gate fixture. Keep the production runner's current-day check
unchanged.

## Why

The test previously read the real clock inside the sealed base driver. It failed
after UTC midnight even though its gate was deliberately fixed to the prior day.
Binding the test clock preserves the runtime expiry interlock while making the
offline validation deterministic.

## Links

- [Phase-one reconciliation](0112-bind-phase-one-replay-to-retained-identities.md)
