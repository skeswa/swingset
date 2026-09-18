# D-0112: Bind phase-one replay to retained identities

Recorded: 2026-09-17  
Decided by: agent  
Topic: Phase-one reconciliation  
Supersedes: —  
Superseded by: —

## Decision

Bind the newsletter replay to each retained catalog, ledger, snapshot, watch and
admission identity. Permit the one observed HTTP-to-HTTPS URL transition only
through its exact target and URLs. Verify generated rows with the runtime's
ordinary-capture watch key and the sealed replay run and time.

## Why

The first two dry seals exposed one retained redirect and an obsolete assumed
generation key. Explicit bindings preserve those facts without allowing other
URL or admission differences.

## Links

- [Phase-one review](../investigations/2026/v2-phase-one-resume-review-2026-09-17.md)
- [Seal decision](0111-seal-phase-one-replay-before-execution.md)
