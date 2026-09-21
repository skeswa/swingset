# D-0181: Answer the year gate once per offers pass, and skip the pass under backpressure

Recorded: 2026-09-21  
Decided by: agent  
Topic: Scheduling  
Supersedes: —  
Superseded by: —

## Decision

The historical offers pass evaluates the phase-two year gate once per year
and reuses the answer for every page of that year; it stops at the
acquisition phase deadline and offers the remaining pages on a later pass;
and the cycle skips the pass while backpressure is active, since no
historical request could be admitted anyway. Nothing else about admission,
dispatch or the gate's meaning changes: dispatch still repeats every gate
before it mutates anything.

## Why

After the hold was lifted, the first cycle spent 2,668 seconds in its
acquisition phase issuing zero requests, so the offline phase never ran and
the 31,821-unit parse backlog could not drain. Profiling the pass read-only
on the live database found the cost: 6,247 plan pages, and for each page the
year gate walked all 32,445 pending projection units, 0.41 seconds a page,
43 minutes a pass. The gate's answer cannot change within one pass.

## Links

- [Candidate 008 receipt](../evidence/runtime/candidate-008-2026-09-21/receipt.json)
- [Candidate 007 receipt](../evidence/runtime/candidate-007-2026-09-21/receipt.json), where the stall was first seen
