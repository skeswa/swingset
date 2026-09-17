# D-0072: Profile offline selection before changing replay

Status: Proposed  
Recorded: 2026-09-17  
Accepted: —  
Acceptance source: —  
Topic: Bounded selector performance investigation  
Supersedes: —  
Superseded by: —

## Decision

Profile one ordinary offline selection against the same held disposable scratch
used for extension input acceptance. Bind the helper and complete runtime to
source 003. Use SQLite read-only mode, denied mutation statements and the existing
writer lock. Execute no parse, project or link worker, HTTP request or publication.
Bound the measured call with both a 30-second Python alarm and a SQLite progress
interrupt; retain the profile even when selection times out.

## Why

The first retained scratch drain committed one parse and three projections in a
550-second selection turn. It preserved all 4,931 named judges and operating
controls but left 31,820 parse hints. The turn stopped at its configured selection
boundary; final preservation verification explains the later process exit.
This is a throughput problem to measure, not evidence of a deadline failure.

The exact source contains complete scope discovery and readiness checks before
worker admission. A blocked kind may scan its entire candidate population before
another kind runs. Link readiness opens a transaction that disables the existing
mutable-transaction memo cache, then loads the complete dancer cohort. These are
concrete possible repeated costs; profiling must establish their actual share
before a runtime change is chosen.

## Consequences

Keep the baseline selector's `query_only=0` behavior for measurement while the
read-only database URI forbids writes. Record current/desired/ready calls by stage
and kind, readiness transaction mode and a cumulative profile. Omit SQL trace
callbacks, whose callback error handling could interfere with the Python alarm.
The profiler's added overhead is explicit; timings are not production throughput.

A later fix must preserve exact dependency currentness, lost-hint discovery,
control freshness and safe invalidation after writes or rollback. This decision
does not authorize making mutable-transaction caches reusable or treating pending
queue rows as the complete derivation population.

## Links

- [Selector investigation](../investigations/2026/offline-selector-profile-2026-09-17.md)
- [Work isolation and fair progress](../../docs/reference/recovery/work.md)
- [Derivation work is a query](../../docs/reference/recovery/requirements.md#derivation-work-is-a-query)

## Measured follow-up

The retained baseline profile measured 978 map `desired` calculations in 30
seconds, repeatedly rebuilding the same map continuity support while rejecting
977 event candidates. It did not reach link readiness. Based on that evidence,
add an owned read-only snapshot around ordinary offline selection and a
2,048-entry LRU of exact currentness answers within that snapshot. Cache keys
include the work unit and context. A repeated negative prerequisite answer is
reusable only while the same snapshot and inputs apply.

Leave caller-owned mutable transactions, inline derivation groups and build
filesystem checks outside this cache. Preserve candidate order, complete scope
fallback and admission's fresh control/dependency checks. Reject callback writes,
restore connection state on every exit, and discard cached answers when the
snapshot closes. This removes measured duplicate work without promoting a queue
hint or cached desired fingerprint into durable authority.

Focused tests establish semantic equivalence and invalidation behavior. A
separate comparison source and scratch profile will establish measured benefit;
these local changes are not evidence of deployment or operating acceptance.
