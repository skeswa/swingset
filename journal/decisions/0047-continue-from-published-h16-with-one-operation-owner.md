# D-0047: Continue from published H16 with one operation owner

Status: Proposed  
Recorded: 2026-09-17  
Accepted: —  
Acceptance source: —  
Topic: Integration and recovery preparation  
Supersedes: —  
Superseded by: —

## Decision

Use three bounded implementation branches in the current shared checkout.
The coordinator alone owns source freezes, schema integration, formatting,
production operations and publication. Preserve the committed identity-link
refactor. Capture the published H16 state through its exact frozen runtime,
without accepting the checkout's differing overrides or migrating production.

## Why

The owner explicitly requested parallel implementation on 2026-09-17 and
retained existing deployment authority. Current inspection found a clean
working copy above `453174fb`; the formerly concurrent refactor is committed.
Production still uses schema 14 while the local extension changes later schemas.
The external event aliases differ from the released bundle. A fresh checkpoint
and read-only review export establish a recoverable starting point without
silently changing accepted inputs.

This records the implementation choice, not new owner acceptance of detailed
policy, historical years, source exceptions or human identity labels.

## Links

- [Continuation evidence and graph](../investigations/2026/v2-continuation-2026-09-17.md)
- [Production authority](0029-resume-h16-production-release-after-validation.md)
- [Backup contract](../../docs/reference/operations.md#backup-and-restore)
