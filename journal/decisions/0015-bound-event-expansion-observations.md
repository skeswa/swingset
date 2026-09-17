# D-0015: Bound verification before gating event-index expansion

Status: Proposed  
Recorded: 2026-09-16  
Accepted: —  
Acceptance source: —; agent recommendation supporting authorized plan work, with no operating acceptance.  
Topic: Scheduling  
Supersedes: —  
Superseded by: —

## Decision

Recommend a separate bounded verification worker that produces conservative
scheduling observations of local acquisition and interpretation. Query compact
records when deciding whether to start another source event. Keep observations
separate from stage authority, with no persisted completion flag and no claim
about complete pagination. This boundary is **not implemented**.

Only actual event-specific work, retained event-specific evidence, or admitted
result obligations start pressure membership. Preserve essential discovery and
started-event continuations. Keep existing host/class budgets and gates; borrowing
must not bypass closed admission for additional events.

## Why

Counting every unfetched event index can close admission before any event starts.
Whole-event inventory and current archive reads have no suitable verification
budget. A correct observer needs resumable scans, bounded artifact reads, explicit
invalidation, and expiry based on the earliest checked evidence.

## Alternatives

- Recompute inventory during selection: archive and history work would grow in
  the request path.
- Cache a completion flag: it could hide corrupt, revoked, or newly listed
  obligations and blur scheduling with stage evidence.
- Infer thresholds from watch counts: raw demand does not establish verified
  local gaps or eligible service.

## Consequences

Unknown, stale, and incomplete checks retain pressure. File corruption may remain
undetected by scheduling within the declared observation window; live drilldown
still checks current bytes. Mapping and publication gaps do not cause downloads.
Thresholds, refresh budgets, observation age, and permanent-gap disposition need
further implementation and measurement. No calibrated defaults are established.

## Links

- [Investigation and proposed persistence seam](../investigations/2026/event-expansion-watermark-2026-09-16.md)
- [Existing protected-capacity choice](0012-protect-listed-page-capacity.md)
- [History and recovery plan](../../docs/plans/history-and-recovery.md)
