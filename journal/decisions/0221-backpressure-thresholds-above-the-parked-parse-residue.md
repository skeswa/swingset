# D-0221: Keep backpressure thresholds above the parked parse residue

Recorded: 2026-09-29  
Decided by: agent, 2026-09-29, under D-0183  
Topic: Acquisition  
Supersedes: the default `pending_parse_items` (1,000) and `pending_parse_bytes` (128 MiB) in production  
Superseded by: in part, [D-0230](0230-keep-parse-backpressure-thresholds-through-phase-2.md): the thresholds stay through phase 2

## Decision

Until phase 2's schema 34 counts only runnable parse work, production sets
`pending_parse_items = 4000` and `pending_parse_bytes = 512 MiB` in
`[scheduling]`. Return them to the defaults in the phase 2 switch.

## Why

Backpressure counts every `pending_work` parse row. After the phase 1 drain,
about 1,250 of them were parked for good (835 superseded, 387 needing review,
11 failed, 8 waiting, 3 unsupported), above the 1,000 threshold, so fetching
would have stayed held with no runnable parse work at all. That is a guard
failing closed on dead work, which rule 3 of the target architecture forbids.
Scheduling settings are not interpretation inputs, so the change re-queues
nothing.

## Links

- [Target architecture, phase 2](../../docs/plans/architecture.md)
