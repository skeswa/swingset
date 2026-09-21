# D-0193: A dismissed parse failure clears its capture from the year gate

Recorded: 2026-09-22  
Decided by: agent, 2026-09-22, under D-0183  
Topic: Review authority  
Supersedes: —  
Superseded by: —

## Decision

A phase-one capture whose parser failed stays a gap for every year it could
cover until a reviewer dismisses that failure in `finding_reviews.csv`. Once
every open finding on the capture is dismissed, the capture no longer counts,
the same way a dismissed warning on a parsed capture already did not (D-0187).
The year findings are recomputed by the phase-one intake and review commands,
which is where dismissals take effect; the cycle's history unit only
reassociates placements and rebuilds coverage.

The 2026-09-22 resynchronization ran the review command from the working
copy, which carried this rule before it was deployed. The findings it wrote
are derived state that the next review recomputes. The rule shipped with the
05:45 UTC switch on 2026-09-23 (candidate 015), which also raised the size
cap (D-0194).

## Why

Four 2016 captures of the WSDC events map hold markers with no name or
printed date. Their parse failures were reviewed and dismissed on 2026-09-21,
but a capture in `finding` status was a gap unconditionally, so the four kept
every year from 2010 on unacceptable. The same review had also dismissed 87 of
89 open phase-one findings, yet none of that reached the year gate, because
nothing in the cycle recomputes it; only the intake and review commands do.

## Links

- [Receipt](../evidence/runtime/v5-year-acceptance-2026-09-22/receipt.json)
- [Backfill reference, events first](../../docs/reference/backfill.md#events-first)
