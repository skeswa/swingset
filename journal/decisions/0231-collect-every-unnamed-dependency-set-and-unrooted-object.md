# D-0231: Collect every unnamed dependency set and every unrooted object

Recorded: 2026-10-02  
Decided by: agent, 2026-10-02, under D-0175; source: phase 4 of the architecture plan, adopted by the owner on 2026-09-25  
Topic: Retention  
Supersedes: the "no other row kind" limit of [D-0200](0200-collect-release-closure-proofs-nothing-names.md), which it extends from closure proofs to every dependency set  
Superseded by: —

## Decision

One mark-sweep collector (`state/collector.py`) removes what no root names
and is older than the start of yesterday (UTC):

- a dependency set that no generation, equivalence, scope proof, recipe
  (continuity or release closure), candidate directory or rooted set names,
  and that was not written in that window;
- a `derivation_output`, `parse_body`, `label_body` or `release_manifest`
  object, and a partition artifact, that its class's root rule does not name;
- `objects/.writing-*` files, files under `objects/` with no row, and the
  row references of a partial generation that no pointer, equivalence or
  building row names (D-0217). The label stays.

The first production run is `gc --collect --plan`, then `gc --collect
--apply <digest>` with the independent check. After that, maintain runs the
sweep weekly. Before schema 36 only sets are collected, under
`operator-hold`.

## Why

I6 says every durable byte needs a collector; D-0200 reached only proofs, and
phase 4 adds four object classes. F39 counted 15.8 GB of sets no generation
names directly. Followed through set-to-set names, as `leaves()` and closure
validation read them, almost all of it is named: the proofs by build labels'
`release_closure` members, the `input_row` sets by old inventory labels. On the
diag copy the rule frees 598,087 sets, 0.10 GB. Freeing the rest means dropping
the dependency record of labels no retention root reaches; that is a separate
data-model decision, not taken here.

## Alternatives and consequences

A set named only inside another set's JSON is kept by following
`"fingerprint"` names, so a false match keeps more, never less. A deleted
set has no copy; every delete rechecks its rule in its own transaction.

## Links

- [State reference: the collector](../../docs/reference/state.md#collector)
