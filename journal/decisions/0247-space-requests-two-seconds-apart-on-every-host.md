# D-0247: Space requests two seconds apart on every host

Recorded: 2026-10-12  
Decided by: owner, 2026-10-12; source: "let's drop this to 2 s per host permanently - no need for this kind of self rate limiting"  
Topic: Fetch politeness  
Supersedes: the 5 s floor of `docs/reference/fetching.md` and `AGENTS.md`, and its 2 s registry-sweep exception  
Superseded by: —

## Decision

The floor between two requests to one host is 2 s, on every host, for good.
The gap is the largest of the host's `min_gap_seconds`, its robots.txt
`Crawl-delay` and 2 s. `config/hosts.toml` sets 2 s for worldsdc.com,
points.worldsdc.com, eepro.com, scoring.dance, scores.worlddanceregistry.com
and danceconvention.net, and a host with no block defaults to 2 s. Config
refuses a gap under 2 s. web.archive.org is not a WCS site and keeps its own
10 s, which the history code also enforces.

These stay: one request in flight per host, robots.txt rules and
`Crawl-delay`, 429 and 5xx backoff and host pauses, conditional requests,
the project User-Agent, and the daily request budgets. Legacy spacing
baselines keep the 5 s floor that migration 28 fixed in their immutable
table. The dataset card now states the 2 s floor.

## Why

The owner judged the 5 s floor to be needless self-limiting for the WCS
sites. At 2 s a host can take at most 43,200 requests a day, so the daily
budgets, not the gap, still bound the load.

## Links

- [Fetching reference](../../docs/reference/fetching.md)
