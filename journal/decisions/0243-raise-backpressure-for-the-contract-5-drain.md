# D-0243: Raise backpressure limits for the contract 5 re-parse drain

Recorded: 2026-10-09  
Decided by: agent, 2026-10-09, under D-0172  
Topic: Acquisition  
Supersedes: for this drain only, the thresholds of [D-0230](0230-keep-parse-backpressure-thresholds-through-phase-2.md)  
Superseded by: [D-0244](0244-parse-backpressure-sits-above-the-superseded-residue.md), after the drain

## Decision

While the contract 5 re-parse drains, production sets `pending_parse_items =
50000`, `pending_parse_bytes = 2147483648` and `pending_work_items = 120000`.
After the drain's post-check they return to 4000, 536870912 and the default
10000 (D-0230).

## Why

Activating contract 5 queued 38,075 parse, 33,417 project and 2,936 link units
at once, all re-reads of pages already stored. Backpressure counts them, so
acquisition held every request from 19:52 UTC. Derive clears about 6,100 units
an hour, so fetching would have stopped for about 11 hours. The backlog is
finite and adds no fetching load; backpressure exists to stop fetching from
outrunning parsing of new pages, which a re-parse does not do. A scheduling
setting re-queues nothing.

## Links

- [Receipt](../evidence/runtime/contract5-2026-10-09/receipt.json)
