# D-0130: `retain_output` is the one writer of derivation output

Recorded: 2026-09-18  
Decided by: agent  
Topic: Derivation output storage  
Supersedes: —  
Superseded by: —

## Decision

One function in `state/derivations.py` writes retained output:

```
retain_output(conn, generation_id, rows, *, record_label=None)
```

It needs an open transaction it can roll back. It never touches
`derivation_scopes`, a pointer, or any scheduling state. It raises
`SupersededWorkError` when the generation does not end up matching its label's
`output_digest` and `row_count`. It reads `rows` in one pass and never holds a
generation in memory.

A generation that has no label yet belongs to a caller completing it. That
caller passes `record_label`, a function that writes the label. `retain_output`
streams the rows into storage, hands `record_label` the digest and row count of
what it stored, then reads the recorded label back and compares. References are
written before the label; their foreign key to `derivation_generations` is
deferred, so a commit still refuses a reference whose label never arrived.

With a label already recorded, residency decides what happens:

- every row is there: nothing to do. Whatever wrote those references already
  checked them against this same label, and references and payloads are both
  immutable, so a reference count and one "any bytes missing?" probe are proof
  enough.
- the references are there but some payload bytes are not, which is what plan
  step 4 leaves behind: store the missing bytes, ignoring any offered row the
  references do not name, then read the whole generation back and check it.
- there are no references: store each payload once, write one reference per row,
  and check the stream.

`derivations.complete` keeps every check it had, and is already inside the
output transaction. A new generation streams its rows through `retain_output`,
which stores them and records the label from what it stored; completion then
rechecks the selected inputs and moves the pointer as before. A repeat of an
existing generation fingerprints its rows in the same single pass, writes
nothing, and is still rejected when the digest or row count differs.
Completion gains no "historical" mode.

## Why

Plan step 4 brings archived output back into a generation whose label and
references are already there. Completion cannot do that job: it rejects inputs
that are not current, which is exactly what an old generation's inputs are. One
narrow writer serves both, and because it checks against the label rather than
against its caller, restored data is checked the same way freshly computed data
is.

Residency, not the presence of references, has to pick the branch. A restored
generation still has every reference, so a function that stopped at "references
exist" could never put a single byte back.

Completion's repeat path does not ask about residency at all. Asking costs a
probe of every reference of the generation, which is exactly the work this step
exists to avoid on an unchanged recomputation, and completion has nothing to put
back that plan step 4's `gc --restore` cannot put back better: that command has
the archived object, completion only has rows it recomputed. So a repeat writes
nothing and reads no stored row, as it did before migration 30.

The transaction is part of the contract because references are permanent. On an
autocommit connection a retention that failed halfway would leave references
nobody can delete and no later call can complete, so the generation could never
be retained or restored again.

The label has to exist before it can be checked against, but buffering the rows
to fingerprint them first is not the way to get there. `project/history/all`
yields 200,597 output rows on the production corpus (66 coverage, 2,541 events
and 197,990 registry placements, counted in the
[H16 production execution record](../evidence/releases/h16/h16-performance-production-execution.json)).
Holding them costs about 120 MB of Python objects, measured here with
`tracemalloc` on the real placement row shape, 596 bytes a row, on the same
worker whose disk
[D-0133](0133-bound-the-worker-disk-and-remove-rehearsals-eagerly.md) had to
bound, and it grows with the registry. The scopes that already materialize
continuity support build 2,945 rows, so buffering would have been a new worst
case, not an existing one.
Handing the label writer down into the streaming pass keeps memory flat and
canonicalizes each payload once instead of twice.

On this path the check is against the stream as it was stored, not a re-read of
the generation. `PRIMARY KEY(generation_id,ordinal)` and
`UNIQUE(generation_id,table_name,record_key)` make the stored references an exact
image of that stream, and `INSERT OR IGNORE` on `derivation_payloads` can only
skip a payload whose digest already matches, so a re-read could only repeat what
was just hashed. The restore path, where the bytes come from outside, does read
the generation back.

A repeat of an unchanged computation reads and writes no stored row at all. It
fingerprints the rows it was handed and compares. That is what makes a
recomputation that changes nothing cost nothing, which is the point of the whole
step.

## Alternatives

- Let completion keep writing rows and add a second function for restore.
  Rejected: two writers of the same permanent records, and restore would not get
  completion's checks.
- Pass the expected digest and row count into `retain_output`. Rejected: the
  caller would then be the authority on what the output should be, which is the
  label's job, and a restore would check its data against itself. Counting rows
  saves the same repeated work without moving that authority.
- Recompute the digest on every call, including for a generation that already
  reads back whole. Rejected: an unchanged recomputation would read and hash its
  whole output a second time, on the one path this change exists to make cheap.
- Buffer the generation in completion so the label can be written first.
  Rejected: it costs about 120 MB on the largest scope and grows with the
  registry, for a check the two unique constraints already give.
- Read the whole generation back through the view after writing it, on every
  completion. Rejected: it doubles the work of the one path this change exists
  to make cheap, and can only confirm the hash just taken.
- Have completion write the label itself after `retain_output` returns the
  digest. Rejected: the caller would then decide what the label says about data
  the writer stored, and nothing would compare the two.

## Consequences

Completion and restoration share one checked path, and no caller can write a row
reference without proving it matches the label. Output rows are never held in
memory, whatever the scope's size. A caller that passes rows for a generation
that is already stored whole gets a count and a probe, not a write, so callers
must not rely on the iterator being consumed. Every caller must open its own
transaction first, and a caller that passes `record_label` is refused if the
generation already has a label.

## Links

- [Implementation plan, section 6](../../docs/plans/bounded-state-and-archive.md)
- [State contract](../../docs/reference/state.md)
- [Layout](0128-intern-derivation-payloads-behind-a-view.md)
- [No foreign key to payloads](0134-let-references-outlive-payload-bytes-without-a-foreign-key.md)
- Renumbered: interning is migration 32, not 30, since [D-0167](0167-intern-derivation-payloads-in-the-last-migration.md), which also gives `retain_output` its pre-interning path.
