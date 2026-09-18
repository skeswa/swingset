# D-0109: Mask ordinary units through the schema-29 switch

Recorded: 2026-09-17  
Decided by: agent  
Topic: Held production deployment  
Supersedes: —  
Superseded by: —

## Decision

Apply runtime masks to the six ordinary cycle, backup and summary services and
timers before switching to candidate 006. Recheck the operator hold and masks
after the switch, and keep both interlocks in place through the schema-29
migration and later production-input review.

## Why

The NixOS activation command can start enabled timers while replacing unit
files. The persistent operator hold stops guarded service work, while runtime
masks also prevent timer activation during that short transition. No ordinary
work needs to run for the held schema migration.

This is an authorized deployment safeguard under D-0087. It does not accept
production inputs, resume collection, activate repairs or publish.

## Links

- [Held migration boundary](0098-separate-held-schema29-migration-from-input-acceptance.md)
- [Operating guide](../../docs/guides/operation.md)
- [Standing authority](0087-authorize-remaining-v2-acquisition-and-operations.md)
