# D-0212: Cut off across the moved coverage column and for held parses

Recorded: 2026-09-26  
Decided by: agent, 2026-09-26, under D-0175 and D-0183; source: the operator's resolution of phase 1's open items in the [architecture plan](../../docs/plans/architecture.md)  
Topic: Derivation identity  
Supersedes: in part, [D-0205](0205-cut-off-identical-derivation-output.md) (what counts as identical output, and which parse generations a parse may keep)  
Superseded by: —

## Decision

**Moved column.** Phase 1 moves `events.coverage_tier` to history, so the
output of a calendar, map, event or dancer scope leaves it out, while that
scope's generations from before the switch recorded it. The cutoff comparison
takes exactly that column out of the kept generation's stored rows before it
compares labels (`materialization.moved_columns`,
`derivations._same_output`). Closure stability (F1), the replay witness (F2)
and pinning (F3) still apply unchanged. History's own rows keep the column.

**Held parses.** For an enforced kind, a parse may also keep the unit's
newest generation when it is held in `needs_review` or `waiting_for_inputs`,
its bytes are equal, and admission would hold the new parse in the same
state: the contract is unreviewed (`needs_review`), or it is reviewed and the
report's own failures decide. The parse records an equivalence and reports
that state; no generation and no admission decision is written. A hold that
reads live state (manifest checks, retained rows) is not kept.

## Why

The first switch minted about 6,300 generations and withheld every link until
the links re-ran (R2), only because the `wsdc` calendar's kept generation
recorded the moved column. A published row takes `coverage_tier` from
history's generation, which the build applies last, so keeping the older id
changes no published value. Held units minted an identical generation on
every re-parse (17 of 2,000 in R3).

## Links

- [Local state, H15](../../docs/reference/state.md)
