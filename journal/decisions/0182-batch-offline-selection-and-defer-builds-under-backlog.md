# D-0182: Batch offline selection, defer builds under backlog, park unsupported page kinds

Recorded: 2026-09-21  
Decided by: agent  
Topic: Scheduling  
Supersedes: —  
Superseded by: —

## Decision

Three changes so a backlog drains in hours rather than weeks:

- The offline phase selects up to 64 units per read snapshot instead of one.
  A batch comes from one selection group, service is still recorded per
  unit, and every unit is re-validated when derived, so a stale pick is
  superseded rather than wrong.
- While backpressure is active, the cycle rebuilds and publishes at most once
  per `scheduling.backlog_publish_interval_seconds`, default six hours. With
  no backlog a changed row still builds every cycle.
- A retained page whose parser this runtime does not know is parked for
  thirty days with reason `unsupported_page_kind` and does not count as a
  failed unit or a failed cycle. The three manual registry-dump snapshots on
  their own replay path (D-0097) were failing every cycle.

## Why

Candidate 008's first cycle had the numbers: 54 units derived in 223 seconds
of offline time, 8.3 seconds of actual work, 4 seconds to pick each unit
because currentness is recomputed for thousands of candidates and the memo
that makes repeats cheap lives only inside one snapshot. The build and
publication took seven of the twelve minutes. At that rate the 31,806-unit
parse backlog would take weeks. Measured on the same database, picks inside
one snapshot after the first are cheap.

## Links

- [Candidate 009 receipt](../evidence/runtime/candidate-009-2026-09-21/receipt.json)
- [D-0181](0181-answer-the-year-gate-once-per-offers-pass.md), the acquisition stall fixed just before
