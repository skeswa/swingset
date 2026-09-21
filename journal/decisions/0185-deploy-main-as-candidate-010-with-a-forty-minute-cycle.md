# D-0185: Deploy main as candidate 010 with a forty-minute cycle

Recorded: 2026-09-21  
Decided by: agent, under D-0183  
Topic: Release operations  
Supersedes: —  
Superseded by: —

## Decision

Candidate 010 is the working copy of `main` at the revision the receipt
names, built as a path flake without `journal/evidence`. It migrates the
production database from schema 29 to 32 on first open, after a local
checkpoint taken under hold. The cycle budget rises from 12 to 40 minutes;
the 15-minute timer skips ticks while a cycle runs, so cycles run back to
back and the fixed per-cycle work stays small next to the offline phase.

## Why

Three hand-assembled candidates on the candidate 006 base were enough to
get the pipeline moving, but every further fix would have to be patched
twice. `main` carries the same fixes plus Step Right admission, the DCN PDF
parser, the restore-activation fix, accept-year, the CDX indexing step and
the nested-span rule. The full suite passed on `main` before the last two
changes and the core suite after them. The migration was measured on a copy
of this database: 92 seconds, 34 seconds to reclaim, 1.9 GB of headroom, a
file 6 percent larger; interning brings no saving yet (D-0166), only the
cost above, and that cost is accepted to stop diverging from `main`.

Candidate 009's cycle spent 176 seconds in reconciliation and 548 in the
offline phase of a 12-minute budget; a 40-minute budget gives the drain
about 36 minutes of every 40.

## Links

- [Candidate 010 receipt](../evidence/runtime/candidate-010-2026-09-21/receipt.json)
- [D-0183](0183-agents-also-decide-deployments-and-publication.md)
