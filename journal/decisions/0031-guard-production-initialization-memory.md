# D-0031: Guard production initialization memory

Status: Proposed  
Recorded: 2026-09-16  
Accepted: —  
Acceptance source: Coordinator review within authorized H16 release work; no separate owner acceptance recorded.  
Topic: Release supervision  
Supersedes: —  
Superseded by: —

## Decision

Run the unchanged production initializer supervisor through a separately
reviewed memory-guard adapter. Version 003 retains version 002 behavior and
binds the supervisor to the missing-recipe compatibility fix in
[D-0032](0032-handle-missing-legacy-runtime-recipe-during-initialization.md). It checks
the exact supervisor hash, samples the worker cgroup's anonymous memory during
bounded waits, and stops at 6 GiB or unavailable live telemetry. The existing
supervisor owns cleanup, waits for the writer lock, and launches no further
batch after failure.

## Why

The reviewed supervisor already protects source, control, and input authority,
but lacks the memory guard used for scratch replay. Reuse its cleanup path
instead of coordinating a second independently running watchdog. Keep the
frozen application source unchanged. The later initializer compatibility fix
changes only its absent-row handling and the dependent helper hashes.

## Bounds and evidence

Split waits into at most five seconds; system queries and reads add overhead.
This cannot prevent brief between-sample overshoot. Permit a bounded startup
transition and ordinary collected-unit exit. Old clean exited units with no
process or cgroup do not count as live work. Other live or unassessed H16 units
block launch; the coordinator still serializes unrelated heavy jobs.

Write exclusive mode-0600 samples inside the private VM-local session. A
replaced sample pathname fails closed. Preserve version 001 and its test
receipt; review found that it rejected old retained exited units and could
mistake a normal exit for failure to start. Version 002 fixes both cases.

Independent final initialization verification will use a separate post-prepare
read-only anchor for exact parse-token and inherited-open-run comparisons.
Preparation may enqueue parse work, but project/link initialization must not
consume or alter it.

The first live anchor stopped because read-only SQLite created a zero-byte WAL
sidecar. The main database's size and mtime were unchanged; the sidecars had
correct service ownership. Preserve that failed receipt and retry the unchanged
verifier with a fresh output after confirming the now-present sidecar. Keep the
strict before/after check rather than weakening it to ignore sidecar changes.

The later production phase launcher reuses the same checked serial-work and
memory-reading helpers. Build, audit, and publication each have a separate
reviewed gate and a 3,600-second worker bound. A vanished cgroup is accepted
only after the exact worker is terminal with no PID; direct process exit still
determines success. This avoids reporting a normal exit as missing live telemetry.
Version 003 of that launcher passed 22 focused tests and coordinator review.

## Links

- [Guard review addendum](../evidence/releases/h16-event-preservation-2026-09-16/production-preparation/monitoring/review-addendum-002.md).
- [Offline guard validation](../evidence/releases/h16-event-preservation-2026-09-16/production-preparation/monitoring/offline-checks-002.json).
- [Production release](../investigations/2026/h16-production-release-2026-09-16.md).
