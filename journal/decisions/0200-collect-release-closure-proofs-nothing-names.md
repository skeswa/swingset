# D-0200: Collect release-closure proofs that nothing names

Recorded: 2026-09-24  
Decided by: agent, 2026-09-24, under D-0183  
Topic: Retention  
Supersedes: the immutability of `derivation_dependency_sets` (migration 14), for release-closure proofs only  
Superseded by: —

## Decision

`swingset gc --closures` lists the release-closure proofs in
`derivation_dependency_sets`. `gc --collect-closures <digest>` removes those
that nothing names. A proof is kept when any of these names it: a
generation's dependency set; a build generation's `release_closure`
dependency; a build recipe; or any directory under `candidates/`, including
the baseline and rejected candidates. Removal recomputes the plan under both
locks and stops if it moved. It lifts the no-delete trigger inside one
transaction per proof and writes a receipt. `gc --reclaim` then shrinks the
file. No other row kind may be deleted this way.

## Why

Failed build attempts left 146 of the 157 proofs, 33.9 GB of the 64 GB file. No
candidate, generation or published manifest names them, so no reader can reach
them; `closure.hydrate` looks proofs up only by a digest that a candidate or
generation carries. Nothing in the tree could delete them, and the size cap
was raised three times instead. Every production run of the collector
deletes data, so it needs an independent check and a verified backup first.

## Links

- [Stability plan](../../docs/plans/stability.md)
