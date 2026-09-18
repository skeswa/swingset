# D-0103: Measure retained replay attempt intervals

Recorded: 2026-09-17  
Decided by: agent  
Topic: Replay diagnosis  
Supersedes: —  
Superseded by: —

## Decision

First query the attempt timestamps already retained by two completed,
100-attempt schema-29 scratch drains. Use the reviewed SELECT-only aggregation
on the exact closed scratch and run IDs. Test its interval calculation on a
small disposable fixture before running it. Preserve the query and output.

Separate attempt execution intervals from the gaps preceding attempts. Do not
label the latter as pure selector time: they also include service accounting,
admission, derivation capture and transaction work. Neither figure establishes
sustained throughput or fixed-cohort service acceptance.

## Why

The two bounded turns completed 197 successful units in about 816 seconds,
with more than 31,000 parse units still queued. Existing receipts do not show
where that time went. Retained timestamps can narrow the next measurement
without running more work or introducing another profiling driver. Keep the
previous isolated trigger benchmark's narrower interpretation unchanged.

## Links

- [Timing query and evidence limits](../investigations/2026/schema29-replay-throughput-next-2026-09-17.md)
- [Successor rehearsals](../investigations/2026/schema29-successor-rehearsals-2026-09-17.md)
- [Isolated trigger measurement](0094-measure-schema29-cost-on-a-disposable-copy.md)
