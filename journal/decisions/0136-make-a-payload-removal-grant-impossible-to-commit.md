# D-0136: Make a payload removal grant impossible to commit

Recorded: 2026-09-18  
Decided by: agent  
Topic: Derivation output storage  
Supersedes: —  
Superseded by: —

## Decision

The permission row that opens the `derivation_payloads` delete gate
([D-0131](0131-gate-payload-removal-with-a-permission-row.md)) cannot outlive
the transaction that writes it. Migration 30 adds
`derivation_payload_removal_grant`, a table nothing ever inserts into, and
`derivation_payload_removal_authority.singleton` carries a
`DEFERRABLE INITIALLY DEFERRED` foreign key to it. SQLite checks a deferred key
at `COMMIT`, so a transaction still holding the permission row fails to commit.
The row has to be removed before the commit that removes the payloads.

Two more things close the one remaining hole, a writer that turns foreign keys
off:

- `PRAGMA foreign_key_check` reports the leftover row. That check already runs
  after every migration, in checkpoint verification, and in recovery, so a
  checkpoint carrying an open gate fails verification instead of restoring one.
- Opening the state database deletes any grant it finds and logs
  `derivation-payload-removal-grant-revoked`. Doctor reads the database
  read-only, so it still reports the row before an ordinary open clears it.

`Database.transaction` now rolls back when `commit` raises. SQLite leaves the
transaction open after a failed commit, and without the rollback the next caller
would inherit it as a savepoint.

## Why

The trigger asks whether a permission row exists, not whether this transaction
created it. Before this change a single committed grant switched the gate off
permanently, for every later process, with nothing to detect or clear it: an
apply that returned early on a non-exception path, an interrupted operator
session, or any future writer that forgot the revoke was enough. A checkpoint
taken afterwards carried the open gate into every restore. That undoes the one
schema-level protection on derivation output, which plan section 4 requires
("Removal happens only from a written plan, under the pipeline's own locks").

An always-empty parent table is the only way SQLite offers to say "this row may
exist during a transaction and never after it". There is no transaction-local
table and no transaction identity a trigger can read. Making the constraint
deferred is what allows the row to exist while the deletes run, and making the
parent empty is what stops it existing at commit.

The FK-off case is worth covering because migrations run with foreign keys off
by design, and a direct `sqlite3` session can turn them off at will. Both the
detection and the clearing are cheap, and neither can hide a real removal: the
removal plan in step 3 writes its own note and receipt.

## Alternatives

- Leave the trigger as an existence check and rely on the removal command to
  revoke. Rejected: that is exactly the guarantee that failed. The gate is the
  last guard, so it cannot depend on a caller's `finally` block.
- Keep the permission row in a `TEMP` table. Rejected: a temp table lives for a
  whole connection, not a transaction, so a long-lived writer keeps the gate
  open; triggers in the main schema cannot see temp tables anyway.
- Refuse to open a database whose grant row is set. Rejected: that would brick
  reading, controls, and recovery, which plan section 9 requires to keep
  working, over a condition the safe response is to clear.
- Check the table in Python before every delete. Rejected: it does not survive a
  different connection or a direct `sqlite3` session, which is the reason the
  gate is in the schema.

## Consequences

A grant is live only inside one transaction, and a writer that forgets to revoke
it removes nothing, because its commit fails. The schema carries one extra empty
table. A failed commit now rolls back, which is a small behaviour change for
every caller of `Database.transaction` and the reason a deferred failure does
not leak into the next one. Plan step 3's apply must delete the grant row before
it commits; plan section 7's ordering ("write the permission row, remove the
eligible row data, commit") gains that one step.

## Links

- [Implementation plan, sections 6 and 7](../../docs/plans/bounded-state-and-archive.md)
- [State contract](../../docs/reference/state.md)
- [Removal gate](0131-gate-payload-removal-with-a-permission-row.md)
- [No foreign key to payloads](0134-let-references-outlive-payload-bytes-without-a-foreign-key.md)
