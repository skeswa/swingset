# D-0173: Defer row-layout optimization to schema 32 and prototype exact JSON compression next

Recorded: 2026-09-20  
Decided by: agent, 2026-09-20; authority: [D-0172](0172-delegate-all-project-decisions-to-agents-until-revoked.md)  
Topic: State retention and recovery cost  
Supersedes: —  
Superseded by: —

## Decision

Do not add a standalone migration that rebuilds the schema-29 through
schema-31 inline `derivation_rows` table as `WITHOUT ROWID`. Schema 32 already
replaces that table, so a separate migration would rewrite the database for a
layout that the next held migration removes.

When schema 32 is prepared for deployment, benchmark its actual
`derivation_row_refs` table with and without `WITHOUT ROWID`. Preserve its
primary key, logical-row uniqueness constraint, deferred foreign key, payload
lookup index, immutability, dispatch fences, and `derivation_rows` view
behavior. Adopt `WITHOUT ROWID` in that rebuild only if a direct measurement
preserves those rules and still shows useful file and checkpoint savings.

The next Step 2a prototype is exact compression of the four
`source_generations` JSON documents. It must reproduce every original UTF-8
byte, preserve every dependent identifier and digest, and keep recipe fields
used by SQLite available for indexed queries. Measure physical database and
checkpoint size, migration cost, peak disk, query plans, and read cost on a
disposable copy before choosing a JSON schema. Test compression before
structural interning.

This closes the row-layout part of Step 2a. It does not select a JSON schema,
authorize a production migration, or change the hold on schema 32.

## Why

The full-copy `WITHOUT ROWID` prototype preserved the inline table's integrity
rules and saved 142,528,512 bytes, 2.9% of the compact database, plus
14,769,705 bytes in the deterministic compressed-backup proxy. That is a real
saving, but a standalone migration would pay another full-table rewrite for a
temporary table shape.

Schema 32 replaces inline rows with narrower references and adds a payload
lookup index. The schema-29 result supports testing `WITHOUT ROWID` during that
rebuild; it does not establish the size or query cost of the reference layout.

The four source-generation documents contain 686,890,362 logical UTF-8 bytes.
Independent zlib-6 streams total 116,539,106 bytes. Those figures do not
predict SQLite page savings, but they justify a physical prototype.
Whole-document sharing is small, while structural interning adds more schema
and reconstruction complexity. Exact compression is the narrower first test.

## Alternatives

- Add a standalone `WITHOUT ROWID` migration before schema 32. Rejected because
  it rewrites the full table for a temporary 2.9% saving.
- Apply the schema-29 result directly to `derivation_row_refs`. Rejected because
  the reference table has different row widths, indexes, and access costs.
- Intern JSON structures first. Rejected because exact compression is simpler
  and has the stronger measured size signal.
- Stop after measurement. Rejected because source JSON is a large measured
  cost with a concrete bounded prototype available.

## Consequences

Schemas 30 and 31 remain unchanged. Schema 32 stays held until the
recomputation-history gate in D-0166 is met, and its reference layout gains a
direct comparison gate. Work proceeds with a disposable exact-compression
prototype; no production state changes.

## Links

- [Investigation](../investigations/2026/state-storage-measurement-2026-09-18.md#query-json-and-layout-follow-up-2026-09-18)
- [Bounded-state plan](../../docs/plans/bounded-state-and-archive.md#6a-step-2a-attribute-row-indexes-and-source-generation-json)
- [D-0166](0166-hold-interning-back-until-rows-repeat-and-cut-indexes-first.md)
- [D-0167](0167-intern-derivation-payloads-in-the-last-migration.md)
