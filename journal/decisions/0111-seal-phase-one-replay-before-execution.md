# D-0111: Seal phase-one replay state before execution

Recorded: 2026-09-17  
Decided by: agent  
Topic: Phase-one reconciliation  
Supersedes: —  
Superseded by: —

## Decision

Require a read-only, independently reviewed before-state seal for the exact 28
newsletter parser-8 targets before a disposable replay can execute. Bind resume
markers to that seal, keep the original ledger unchanged, and reject changes
outside the target-derived database and successor-ledger scope.

## Why

The first helper review found that a path check and self-reported bundle hash did
not prove scratch ancestry or exact mutation scope. A separate seal makes the
reviewed baseline explicit and permits fail-closed recovery after interruption.

## Links

- [Phase-one review](../investigations/2026/v2-phase-one-resume-review-2026-09-17.md)
- [Standing authority](0087-authorize-remaining-v2-acquisition-and-operations.md)
