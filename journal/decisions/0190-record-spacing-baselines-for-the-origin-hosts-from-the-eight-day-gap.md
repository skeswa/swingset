# D-0190: Record spacing baselines for the origin hosts from the eight-day gap

Recorded: 2026-09-21  
Decided by: agent, 2026-09-21, under D-0183  
Topic: Fetching  
Supersedes: the per-host proof packet of [D-0062](0062-bind-legacy-spacing-baselines-to-retained-requests.md) for these six hosts  
Superseded by: —

## Decision

Record a legacy request-spacing baseline of ten seconds for the six origin
hosts that never got one: `scoring.dance`, `eepro.com`, `points.worldsdc.com`,
`worldsdc.com`, `www.worldsdc.com` and `scores.worlddanceregistry.com`. The
proof is the gap itself. Each host's last paid request was on 2026-09-12 or
2026-09-13, the worker has made none since, and no retained robots file asks
for a crawl delay. Ten seconds is at or above every configured floor, and the
gate still makes each host wait one full gap before its first request. Daily
request budgets and robots rules are unchanged. The evidence reference stored
with each baseline is the hash of that host's row in the receipt.

## Why

The schema-28 gate refuses a host whose earlier request timing is unknown
("legacy request spacing unknown") until a reviewed baseline exists. Only the
Wayback Machine and danceconvention.net received one on 2026-09-17. So the
worker fetched nothing from any origin host after 2026-09-13 05:54 UTC, while
about 6,100 watches came due. Lifting the operator hold on 2026-09-20 did not
change that: every cycle spent its 1,000-second acquisition phase admitting
about 900 fetch turns that each ended in milliseconds with no request. No
summary field, doctor line or alert showed it, because the gate's refusal is a
skip, not a failure.

D-0062 asked for a pinned proof packet per host because old timestamps cannot
prove a robots delay or a completion time. That concern is about seconds.
After eight days without a request it has no bearing, so the simpler proof is
enough.

## Links

- [Receipt](../evidence/runtime/spacing-baselines-2026-09-21/receipt.json)
- [Fetching reference](../../docs/reference/fetching.md)
