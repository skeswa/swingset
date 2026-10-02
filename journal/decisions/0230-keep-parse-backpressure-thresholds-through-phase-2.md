# D-0230: Keep the parse backpressure thresholds through phase 2

Recorded: 2026-10-02  
Decided by: agent, 2026-10-02, under D-0172, on the phase 2 switch 2a independent check  
Topic: Acquisition  
Supersedes: D-0221's "return them to the defaults in the phase 2 switch"  
Superseded by: —

## Decision

Production keeps `pending_parse_items = 4000` and `pending_parse_bytes =
536870912` through phase 2 and later trees, until backpressure stops
counting superseded parse rows or those rows are cleared. Removing them is
maintenance item 13.

## Why

Schema 34 does not count only runnable parse work, as D-0221 assumed. A
superseded parse keeps its `pending_work` row on purpose and gets no
`work_queue` row from the migration seed, and the runnable-parse count treats
a missing queue row as runnable (`schedule/fairness.py`). Production had 835
such rows on 2026-09-29, and the number only grows. At the defaults of 1,000
items and 128 MiB, dead work would hold fetching again: the D-0221 and D-0190
failure. The rehearsals missed it because they lifted the thresholds.

## Links

- [Phase 2 switch 2a receipt](../evidence/runtime/architecture-phase2a-2026-10-02/receipt.json)
