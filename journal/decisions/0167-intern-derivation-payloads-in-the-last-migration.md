# D-0167: Intern derivation payloads in the last migration

Status: Proposed  
Recorded: 2026-09-18  
Accepted: —  
Acceptance source: —  
Topic: State retention and recovery cost  
Supersedes: —  
Superseded by: —

## Decision

This carries out point 2 of
[D-0166](0166-hold-interning-back-until-rows-repeat-and-cut-indexes-first.md).
The three unreleased migrations change places. `SCHEMA_VERSION` stays 32.

| Schema | Before                       | Now                          |
| ------ | ---------------------------- | ---------------------------- |
| 30     | derivation payload interning | finding support references   |
| 31     | finding support references   | retention apply notes        |
| 32     | retention apply notes        | derivation payload interning |

The contents of each migration are unchanged; only their numbers and the
version each Python hook runs at moved. Interning is now the last migration, so
a database may sit at schema 30 or 31 indefinitely, and step 3 of the
[bounded state plan](../../docs/plans/bounded-state-and-archive.md) deploys
without deploying interning.

Everything that named the interned tables therefore asks first whether they
exist. `derivations.interned(conn)` is that one question: does
`derivation_row_refs` exist as a table. What each caller does when the answer is
no:

- `retain_output` writes whole rows into the one `derivation_rows` table, no
  payload bytes can be missing, and the path that puts missing bytes back does
  nothing but read the generation and check it.
- The plan counts a generation's references, payloads and bytes from that table:
  a row is its own payload, so `payload_count` equals `reference_count`.
- The three-way byte split is by generation instead of by payload, because a
  row's bytes belong to one generation and archiving that generation is what
  would free them.
- `orphan_payloads` and `unowned_payloads` are empty, because there are no
  digests to name. The plan carries `payloads_interned: false` so a reader knows
  which of the two that is, and doctor prints it. Rows whose label is missing are
  still reported, by generation, under `orphan_row_references`.
- `eligible_payloads` returns nothing, so `gc --apply` removes files and no row
  data at all.
- Doctor runs the retention report whenever `derivation_generations` exists,
  rather than whenever the interned tables do.

Reading output through the name `derivation_rows` needs no branch anywhere: it is
a table before the split and a view after it, which is what the view was for.

Two smaller changes go with it. `retention_apply._check_after_rewrite` reads
`SCHEMA_VERSION` when it runs instead of when the module was imported, so a
caller that pins the schema is checked against its own pin. The plan format stays
`retention-plan-v1` although the plan gained a key: a plan is applied only after
being recomputed and digest-matched under both locks, so a plan file written by
older code can never be applied anyway.

## Why

D-0166 measured interning on a scratch copy of held checkpoint 004 and found it
makes the file 6.3% larger, because no scope has been recomputed yet and so no
row repeats. It should not be deployed until rows repeat. But migrations apply in
order, so while interning was schema 30 nothing after it could be applied
without it, and step 3 — the walk, the plan, `gc --apply`, `gc --reclaim`, holds
and the doctor report, which is the main lever on growth — could not deploy at
all.

Moving interning to the end is the smallest change that unties them. The
alternative, making the code tolerate any order, is not needed: schema numbers
are applied in sequence, so the only orders that exist are prefixes.

The conditional code is the price. It is bounded: five call sites plus one
writer, all in `retention.py`, `retention_apply.py` and `derivations.py`, each a
branch on one table's existence. The alternative of leaving those paths to fail
at schema 30 and 31 would mean the schemas this reorder exists to make
deployable are not in fact deployable.

## Alternatives

- Renumber interning as 33 and leave 30 where it is. Rejected: it would leave a
  gap the migration loop refuses, and `SCHEMA_VERSION` would have to advance for
  a migration nobody wants to run.
- Delete the interning migration and re-add it later. Rejected: D-0166 keeps the
  code tested, because recomputation will start when the hold lifts and the ratio
  has to be measured then. Deleting it would throw away the tests and the review.
- Let the retention code fail at schemas 30 and 31 and require interning before
  step 3 deploys. Rejected: that is the situation this record exists to end.
- Guard the code on the schema number rather than on the table. Rejected: a
  checkpoint or a restored copy can be at any schema, and the tables are the fact
  the queries actually depend on. The file closure already asks about
  `finding_support_references` and `archived_generations` the same way.

## Consequences

Step 3 can be deployed on its own, as schemas 30 and 31, with interning left for
a database whose rows repeat. Nothing else about either migration changed, so
D-0128 through D-0131, D-0139 and D-0148 still describe what they describe; each
carries a one-line note of its new number.

The cost is that the retention code now has two shapes to be correct in, and
both have to stay tested. `tests/test_uninterned_schema.py` pins the schema to
31 and runs the plan, apply, reclaim, holds, doctor and a checkpoint and restore
on that database; `tests/test_derivation_payloads.py` still owns the interning
migration and now opens at exactly schema 32. Its interrupted-migration tests
assert the database stops at 31, which is the reorder's whole point: a failed
interning leaves a database an operator can still run.

Byte numbers are not comparable across the migration. A plan written before
interning counts a repeated row once per generation and one written after counts
it once; both are right about their own file.

## Links

- [D-0166](0166-hold-interning-back-until-rows-repeat-and-cut-indexes-first.md), which asked for this
- [Plan](../../docs/plans/bounded-state-and-archive.md), sections 6 and 7
- [Schema history](../../docs/reference/schema-history.md)
- [State reference](../../docs/reference/state.md#retention)
- [D-0013](0013-preserve-historical-migration-tests.md), which is why each migration test opens at exactly its own schema
- [Investigation](../investigations/2026/state-storage-measurement-2026-09-18.md)
