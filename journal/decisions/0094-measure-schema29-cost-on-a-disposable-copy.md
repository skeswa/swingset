# D-0094: Measure schema-29 cost on a disposable copy

Status: Proposed  
Recorded: 2026-09-17  
Accepted: —  
Acceptance source: —  
Topic: Operating measurements  
Supersedes: —  
Superseded by: —

## Decision

Measure schema-29 dispatch-fence trigger cost on a new copy of the passed
migration specimen. Use a fixed set of populated rows and alternate bounded
no-op update samples with and without the exact new triggers inside rolled-back
savepoints. Disable older triggers in both variants within an outer rollback-only
savepoint. Verify exact trigger definitions, fence increments and restored state.
Preserve the specimen, tool bytes and receipt. The coordinator independently
reviews and executes the helper after the measured scratch replay closes.

## Why

Candidate 005 introduces write-trigger work. A paired specimen measurement can
isolate that cost without changing production, a retained checkpoint or the
replay specimen. It supplements the actual replay; it does not measure durable
commit cost, full parsing or fixed-cohort acquisition. Neither measurement by
itself supplies operating acceptance. This is an engineering choice under the
resumed work in D-0093, not new owner acceptance of V6.

The first read-only cohort check found older triggers on every populated target,
so excluding those tables left no measurable rows. No benchmark ran. The revised
diagnostic disables older triggers in both variants and restores their full
inventory afterward. Its result excludes interactions with those triggers. The
[preflight receipt](../evidence/runtime/schema29-successor-rehearsals-2026-09-17/overhead-review-001/cohort-preflight-002.json)
retains that limitation.

## Links

- [Successor rehearsals](../investigations/2026/schema29-successor-rehearsals-2026-09-17.md)
- [Timing contract](../../docs/reference/event-timing.md)
