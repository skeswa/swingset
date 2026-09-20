# D-0174: Benchmark one exact source JSON compression layout

Recorded: 2026-09-20  
Decided by: agent, 2026-09-20; authority: [D-0172](0172-delegate-all-project-decisions-to-agents-until-revoked.md)  
Topic: State retention and recovery cost  
Supersedes: —  
Superseded by: —

## Decision

Benchmark one disposable source-generation JSON layout before considering
additional variants. Call the compact current layout A0 and the candidate A1.

A1 keeps `source_generations` as the metadata, foreign-key and trigger anchor.
It moves the four JSON documents into a one-to-one `WITHOUT ROWID` table named
`source_generation_documents`. Each document has an independent zlib level-6
blob, its original byte count and a 32-byte SHA-256. Deferred foreign keys in
both directions require exactly one document row per generation at commit.

The anchor stores `recipe_bytes`, `recipe_valid`, `recipe_snapshot_id` and
`recipe_source` so existing SQL does not decompress JSON. Its retained-source
expression index becomes an ordinary index on `recipe_source`. Immutability
covers the extracted fields and the compressed document row; the other
generation foreign keys and triggers remain on the anchor.

The benchmark must rebuild fresh full-database A0 and A1 copies, compact both
the same way, and compare their database and compressed-backup-proxy sizes. It
must prove byte-for-byte reconstruction of all four documents, validate every
stored length and digest, preserve the anchor's foreign keys and trigger
behavior, and exercise the existing recipe lookup plans and results.

Do not add structural interning to this benchmark. A1 is a prototype choice,
not a production schema selection or migration authorization.

## Why

The retained profile found 686,890,362 logical UTF-8 bytes in the four JSON
columns and 116,539,106 bytes when each document was independently compressed
with zlib level 6. That establishes a useful candidate, but it does not predict
SQLite page or checkpoint savings.

One candidate isolates the physical value of exact compression and the cost of
keeping SQL-visible recipe facts. A matrix that also interns accepted inputs,
fields or guards would mix compression, reference overhead and reconstruction
logic before the simpler layout has a measured baseline. Keeping the existing
row as the relational anchor also avoids changing generation relationships and
trigger ownership merely to measure payload storage.

## Alternatives

- Benchmark compression and structural interning together. Rejected because it
  would not show whether the added dictionaries and references pay for their
  complexity.
- Compress one combined JSON envelope. Rejected because independent streams
  preserve column-level reads and account for each document separately.
- Replace `source_generations` entirely. Rejected because its metadata,
  relationships and triggers are not the storage question under test.
- Select A1 as the production schema now. Rejected because logical compression
  counts are not physical database or checkpoint measurements.

## Consequences

The first benchmark has exactly two layouts, A0 and A1, and one compression
level. Its result can support a later schema decision, further prototype work,
or leaving the current layout unchanged. Structural repetition remains measured
evidence but is deferred until A1's physical benefit and read cost are known.

No production database, migration, reader or retained evidence changes under
this decision.

## Links

- [D-0173](0173-defer-row-layout-to-schema32-and-prototype-json-compression.md)
- [Investigation](../investigations/2026/state-storage-measurement-2026-09-18.md#query-json-and-layout-follow-up-2026-09-18)
- [Bounded-state plan](../../docs/plans/bounded-state-and-archive.md#6a-step-2a-attribute-row-indexes-and-source-generation-json)
