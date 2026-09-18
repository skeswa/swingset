# D-0131: Gate payload removal with a permission row in the deleting transaction

Status: Proposed  
Recorded: 2026-09-18  
Accepted: —  
Acceptance source: —  
Topic: Derivation output storage  
Supersedes: —  
Superseded by: —

## Decision

`derivation_payloads` gets a `BEFORE DELETE` trigger that aborts unless the
transaction carries a row in `derivation_payload_removal_authority`, a small
table whose single row is inserted and removed inside the deleting transaction.
A deferred foreign key makes that "inside" a rule rather than a habit: the row
cannot commit, so it cannot be left switched on
([D-0136](0136-make-a-payload-removal-grant-impossible-to-commit.md)).
`derivation_payloads` also gets a no-update trigger. Row references keep the
plain no-update and no-delete triggers the inline rows had, and no foreign key
points at `derivation_payloads`, so the gate can actually let a referenced
payload go ([D-0134](0134-let-references-outlive-payload-bytes-without-a-foreign-key.md)).

Nothing in the pipeline writes the permission row today. Plan step 3's removal
apply is the first thing that will, under the writer and control locks, keyed to
a reviewed plan.

## Why

Payload bytes are the one derivation record the plan is allowed to remove, once
they are archived. Everything else, labels and references included, is
permanent. That difference belongs in the schema, so an accidental `DELETE` from
a script, a console, or a future migration fails rather than silently removing
history.

`removal_authority` on `source_generations` already marks which evidence may
ever be removed, and this mirrors its spirit: removal is a declared, narrow act
rather than an ordinary write. A row inside the transaction, rather than a
column or a pragma, ties the permission to exactly the transaction doing the
delete. On its own a row is only a convention, because the trigger asks whether
one exists and not who wrote it; D-0136 turns the convention into a constraint.

## Alternatives

- No trigger, rely on the removal command. Rejected: the schema then makes
  permanent and removable records look identical, and one stray statement is
  enough.
- A column on `derivation_payloads` saying a payload may be removed. Rejected:
  eligibility is decided by a reachability walk over the whole database, not by a
  fact about one payload, and a stale column would be a second source of truth.
- A connection pragma or a Python-side guard. Rejected: neither survives a
  different connection or a direct `sqlite3` session.

## Consequences

Payload bytes cannot be deleted by accident, and the eventual removal step has
to say so explicitly in its own transaction. The permission table is one more
table in the schema, and anything counting application tables sees it. A
transaction that inserts the permission row and crashes removes nothing, because
the row rolls back with everything else.

## Links

- [Implementation plan, sections 6 and 7](../../docs/plans/bounded-state-and-archive.md)
- [State contract](../../docs/reference/state.md)
- [Layout](0128-intern-derivation-payloads-behind-a-view.md)
- [No foreign key to payloads](0134-let-references-outlive-payload-bytes-without-a-foreign-key.md)
- [Grant fence](0136-make-a-payload-removal-grant-impossible-to-commit.md)
