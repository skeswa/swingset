# D-0229: Derivation output and old bodies live in packs, not the database

Recorded: 2026-10-02  
Decided by: agent, 2026-10-02; source: phase 4 of the architecture plan, adopted by the owner on 2026-09-25  
Topic: State retention  
Supersedes: the bounded-state plan's step 4 two-store archive and the `archived_generations` gate of [D-0149](0149-no-generation-is-eligible-until-a-table-says-it-is-archived.md); amends [D-0119](0119-bound-state-by-interning-and-one-closure.md)  
Superseded by: —

## Decision

A generation's output is kept as a pack: one `derivation_output` object
under `objects/`, whose bytes are the label stream and whose name is the
label's `output_digest`. Packs are kept for every label, forever. Current
pointers and building generations stay resident in `state.sqlite`; any other
complete generation may lose its row references and the payloads nothing
else names, once its pack is written and verified. A `derivation_evicted`
row records that, and readers fall back to the pack. Parse bodies and large
build recipes of unprotected rows are packed the same way (WP4). The local
segmented backup is the second copy.

`swingset storage plan` and `storage apply <digest>` do the one-time move
under `operator-hold`; maintain evicts newly cold generations a day old.

D-0119 said labels and row references are permanent. It now reads: labels
are permanent; row references live in the file or as a pack.

## Why

At 64.7 GB the diag copy held 29.2 GB of row references and their indexes,
94% of them for generations nothing points at (F31). A pack named by the
label needs no mapping table, verifies against the label, and dedupes 21×
(about 30,510 packs). The two remote stores of step 4 were never built, so
D-0149's gate never opened and nothing could shrink.

## Alternatives and consequences

Deleting old generations outright would lose reproducibility of past
releases. Packs cost about 0.5 to 1 GB of objects (estimate), a page-in
before a pointer flips to an evicted generation, and phase 3 code cannot
read a moved state: rollback after the move is the byte copy only.

## Links

- [Architecture plan, phase 4](../../docs/plans/architecture.md)
