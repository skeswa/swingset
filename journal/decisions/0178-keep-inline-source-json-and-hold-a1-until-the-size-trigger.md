# D-0178: Keep the inline source-generation JSON layout and hold A1 until the size trigger

Recorded: 2026-09-21  
Decided by: agent, 2026-09-21; authority: [D-0172](0172-delegate-all-project-decisions-to-agents-until-revoked.md)  
Topic: State retention and recovery cost  
Supersedes: —  
Superseded by: —

## Decision

`source_generations` keeps its four inline JSON columns. No production schema
is designed from the A1 compressed-document layout, and no migration, reader
change or deployment follows from its benchmark. This closes step 2a of the
bounded-state plan by recording that the current layout stays.

A1 as specified in [D-0174](0174-benchmark-one-exact-source-json-compression-layout.md)
is the first candidate to revisit if the state-size trigger in `AGENTS.md`
fires: a live database over 20 GB or a worker disk under 100 GB free. The
measurement that reopens it must include a real checkpoint, not the
deterministic gzip proxy, because the proxy moved in the opposite direction
from the database file.

## Why

The full-copy benchmark passed every gate on a disposable schema-29 copy. A1
is sound: exact documents, identities, extracted recipe fields, one-to-one
integrity, immutability, cascade and external dependents all matched, and the
two reads that previously scanned and called `json_extract` became far cheaper.

| Measure                              |   A0 baseline |            A1 |                Change |
| ------------------------------------ | ------------: | ------------: | --------------------: |
| Whole database                       | 4,859,154,432 | 4,305,444,864 | −553,709,568 (−11.4%) |
| `source_generations` and its indexes |   730,222,592 |   176,508,928 | −553,713,664 (−75.8%) |
| Deterministic gzip proxy             |   499,507,387 |   570,988,752 |  +71,481,365 (+14.3%) |

The saving is real but it is not needed. The live database is 5.0 GB on a
worker disk with more than 400 GB free, so the trigger that authorizes this
optimization is nowhere near met, and `AGENTS.md` starts optimization work only
from a trigger written in its plan. Adopting A1 would also put a decode
accessor in front of every full-document read for a database under no pressure.

The proxy regression is the open question. Compressing each document removes
the redundancy a whole-file compressor was already exploiting, so the candidate
file shrinks while its compressed archive grows. That number holds for gzip,
whose 32 KiB window is what A1 defeats; another codec would land elsewhere.
Checkpoints today are uncompressed `connection.backup()` copies, so the proxy
does not describe them either, and what a real checkpoint costs under A1 is
unmeasured. D-0174 asked for that figure, and deciding on the proxy alone would
be deciding on the wrong number.

## Links

- [Investigation](../investigations/2026/state-storage-measurement-2026-09-18.md#full-copy-a0a1-source-json-compression-benchmark-2026-09-21)
- [Receipt](../evidence/runtime/state-storage-measurement-2026-09-18/source-json-compression-receipt-004.json)
- [D-0174](0174-benchmark-one-exact-source-json-compression-layout.md)
- [Bounded-state plan](../../docs/plans/bounded-state-and-archive.md#6a-step-2a-attribute-row-indexes-and-source-generation-json)
