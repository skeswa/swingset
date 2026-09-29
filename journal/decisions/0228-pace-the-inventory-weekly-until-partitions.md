# D-0228: Pace the inventory weekly until partitions ship

Recorded: 2026-10-02  
Decided by: owner, 2026-10-02; source: "I like the rec" (option 1, weekly inventory, config only)  
Topic: Scheduling  
Supersedes: the 48-hour `inventory_interval_seconds` of [D-0227](0227-pace-inventory-and-history-at-48-hours-until-partitions.md), in production; history stays at 48 hours  
Superseded by: —

## Decision

Production sets `inventory_interval_seconds` to 604,800 (7 days) until the
phase 3 switch, which returns it to that tree's own value. History stays at
D-0227's 172,800. If phase 3 has not deployed by 2026-10-09, the fallback
is to raise the cap to about 76 GB by D-0220's procedure.

## Why

The file was 70.99 GB at 04:25 UTC on 2026-10-02, against the 72 GB cap
(D-0220). The two inventory generations of 2026-10-01 23:42 and 10-02 00:05
each re-minted every event and link, about 1.4 GB together, and the next
was due 10-04 00:05. One more cascade would cross the cap and pause the
worker. A weekly event list is enough for a weekly dataset, and a
scheduling setting re-queues nothing.

## Links

- [Receipt](../evidence/runtime/weekly-inventory-pacing-2026-10-02/receipt.json)
