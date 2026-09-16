# Reading pages and combining records

A source page uses its own names, IDs, and layout. Swingset first records what
that source says, then combines it with other evidence about the same event.
These are separate steps because either interpretation can need correction.

## Read the source

A _parser_ turns a saved page into observations: statements such as “bib 42
placed third in this round.” It uses the source's own references rather than
guessing a shared event ID.

_Admission_ means checking whether that interpretation is safe to use. For
example, a changed page layout might hide half the rows from an old parser.
A successful parse alone does not prove that the result is complete.

## Combine related evidence

The matching map connects a source's event to Swingset's shared event record.
A _projection_ combines the observations for that event into dataset records.
It can report conflicts when two sources disagree.

Moving an event match should regroup saved evidence without asking the source
for it again. A failed parse should preserve the last good observations.

## Go deeper

- [Parsing](../reference/parsing.md): adapter interfaces and admission rules.
- [Architecture](../reference/architecture.md): ownership and conflict resolution.
- [Data model](../reference/data-model.md): the shared tables and IDs.
- Code: [admission](../../src/swingset/admission/select.py), [event mapping](../../src/swingset/project/map.py), and [row writing](../../src/swingset/project/writer.py).
