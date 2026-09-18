# D-0129: Verify every generation before dropping the old derivation rows

Status: Proposed  
Recorded: 2026-09-18  
Accepted: —  
Acceptance source: —  
Topic: Derivation output storage  
Supersedes: —  
Superseded by: —

## Decision

Migration 30 renames `derivation_rows` to `derivation_rows_legacy`, creates the
new tables and the view, and then, in a Python hook for version 30 in
`state/db.py`:

1. streams the old table in order, hashing each `payload_json` with sha256 and
   writing one payload row and one reference row;
2. recomputes every generation's `output_digest` through the new view, using the
   exact hashing in `derivations.complete`, and compares it and the row count
   with `derivation_generations`; and
3. drops the old table only if every generation matches, otherwise raises.

The hook runs inside the migration's single transaction, so a raise rolls the
whole migration back with the old table and its rows intact.

The migration records two costs: it needs about twice the old table's bytes free
while the write-ahead log holds both copies, and the drop returns pages to
SQLite's free list without shrinking the file. Reclaiming file space is
`gc --reclaim` in plan step 3, not part of this migration.

## Why

This is the one step that removes the only copy of data the pipeline must keep.
The safety rules in the plan say no step may remove the only checked copy, so
the check has to be the real one: the digest each generation's label already
carries, recomputed from the new tables through the view a reader would use. A
row count or a checksum of the new tables would prove the copy is complete
without proving it reads back the same.

The hook is Python because SQLite has no sha256. Putting it in `_migrate`
alongside the version-2 hook keeps it inside the migration transaction, which is
what makes the step reversible until the drop.

## Alternatives

- Keep the old table forever and stop writing to it. Rejected: it leaves the
  bytes the plan exists to reclaim, and a second copy that nothing checks.
- Drop the old table in a later migration, after a period of running on the new
  tables. Rejected: a second gated production migration for no extra safety,
  since the comparison already proves the new tables read back the same.
- Verify a sample of generations. Rejected: the cost of a full pass is one read
  of data already being written, and a sample cannot show that the generation it
  skipped is intact.
- Reclaim the file inside this migration. Rejected: `VACUUM` cannot run inside a
  transaction, needs about twice the file size free, and holds the locks for the
  whole rewrite. It belongs to its own command with its own receipt.

## Consequences

The migration is reversible until its last statement, and a database that fails
the comparison stays at schema 29 with every fingerprint still checking. It
costs one extra full read and write of the old table, and free disk of about
twice that table's size. The file on disk does not get smaller until reclaim
runs, so the size cap in plan section 9 is unaffected by this migration alone.

## Links

- [Implementation plan, section 6](../../docs/plans/bounded-state-and-archive.md)
- [Schema history](../../docs/reference/schema-history.md)
- [Layout](0128-intern-derivation-payloads-behind-a-view.md)
- Renumbered: the hook runs at version 32, not 30, since [D-0167](0167-intern-derivation-payloads-in-the-last-migration.md).
