# D-0244: Parse backpressure sits above the superseded-row residue

Recorded: 2026-10-10  
Decided by: agent, 2026-10-10, under D-0172  
Topic: Acquisition  
Supersedes: `pending_parse_items = 4000` of [D-0230](0230-keep-parse-backpressure-thresholds-through-phase-2.md) and the drain values of [D-0243](0243-raise-backpressure-for-the-contract-5-drain.md)  
Superseded by: —

## Decision

Production sets `pending_parse_items = 8000` and `pending_parse_bytes =
536870912`, with `pending_work_items` at its default 10,000, until
backpressure stops counting superseded parse rows (maintenance item 13).

## Why

After the contract 5 re-parse drained on 2026-10-10, backpressure counted
3,566 parse units as runnable, almost all superseded rows that keep a
`pending_work` row and never run (D-0230). At 4,000 an ordinary batch of new
pages would hold fetching. 8,000 keeps the 4,400 of headroom D-0221 chose. A
scheduling setting re-queues nothing.

## Links

- [Receipt](../evidence/runtime/contract5-2026-10-09/receipt.json)
