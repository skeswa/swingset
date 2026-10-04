# D-0246: Raise WCS daily request budgets for 24 hours

Recorded: 2026-10-12  
Decided by: owner, 2026-10-12; source: "We got approved to increase our hit rate on all wcs websites for the rest of the day - please waive politeness gate for the next 24 horus"  
Topic: Fetch politeness  
Supersedes: for 24 hours, the daily request budgets in `config/hosts.toml`  
Superseded by: —

## Decision

From 2026-10-12 01:28 UTC to about 2026-10-13 02:00 UTC production allows
10,000 requests a day to points.worldsdc.com, scoring.dance and eepro.com,
5,000 to scores.worlddanceregistry.com, 2,000 (and 3 GB) to
danceconvention.net and 200 to worldsdc.com. web.archive.org is not a WCS
site and keeps its budget. Then the budgets return to the repository values.

## Why

The owner reports approval from the WCS sites to fetch more for a day. The
daily budgets, not the per-host gap, were the binding limit (at one request
every five seconds a host could take about 17,000 a day). One request in
flight per host, robots rules, Crawl-delay, 429 and 5xx backoff and the User-Agent stay.
A host configuration change re-queues no derivation work.

## Links

- [Fetching reference](../../docs/reference/fetching.md)
