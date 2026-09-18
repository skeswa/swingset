# D-0149: No generation is eligible until a table says it is archived

Status: Proposed  
Recorded: 2026-09-18  
Accepted: —  
Acceptance source: —  
Topic: State retention  
Supersedes: —  
Superseded by: —

## Decision

`gc --apply` has a working payload removal path, and it is shut. The gate is one
question: does an `archived_generations` table exist, and does it name this
generation? Plan step 4 adds that table. Until it does,
`retention_apply.eligible_payloads` returns nothing, apply plans no payloads, and
no row data is ever removed.

When the table exists, a payload goes only when every generation that names it is
both archivable and named in that table. One local generation naming a shared
payload keeps it.

The gate is written now, and tested now, rather than left as a hole for step 4 to
fill.

## Why

Plan section 7 says "Until step 4 exists, apply removes no row data", and plan
section 4 says no step removes the only checked copy of any data. The table is
the fact that says a second copy exists, so testing for it is the same test as
"is there another copy". Gating on the table, rather than on a flag or a version
check, means step 4 turns the path on by doing its own job, and cannot turn it on
by accident before it has done it.

Writing the path now buys two things. The permission-row dance from
[D-0136](0136-make-a-payload-removal-grant-impossible-to-commit.md) is exercised
against a real removal, with a test that the grant cannot be left switched on,
instead of being carried untested until step 4. And the shared-payload rule, the
subtle part, is settled while there is nothing to lose by getting it wrong.

The shared-payload rule has to be "every owner eligible", not "any owner
eligible", because payloads are interned. A row that is byte for byte identical
in a current generation and an old one is one row. Removing it because the old
one is archived would take the current one's data with it, and the plan's local
list exists precisely to stop that.

## Alternatives

- Raise "not implemented" until step 4. Rejected: the path would ship untested,
  and the first time it ran would be the first time it ran against real data.
- Gate on a configuration flag. Rejected: a flag can be set by someone who has
  not archived anything. The table is the evidence itself.
- Gate on the full step 4 rule now, two checked copy rows in `archive_replicas`.
  Rejected: that table's shape is step 4's to decide, and guessing it here would
  force step 4 to either match the guess or change this code anyway. Step 4 adds
  the copy-row condition to the same function.
- Remove payloads for any archivable generation. Rejected: archivable means "may
  be archived", not "is archived". That is the difference between this plan and
  deleting on age.

## Consequences

An apply today removes files and nothing else, and a test proves it. The gate is
one function with one call site, so step 4 has one place to extend and one test
file to add rows to. The cost is a code path that does nothing in production
until step 4 lands; the tests that cover it stand up a minimal
`archived_generations` table of their own, which will be replaced by the real one.

## Links

- [Implementation plan, sections 7 and 8](../../docs/plans/bounded-state-and-archive.md)
- [State contract](../../docs/reference/state.md)
- [Gate payload removal with a permission row](0131-gate-payload-removal-with-a-permission-row.md)
- [Make a payload removal grant impossible to commit](0136-make-a-payload-removal-grant-impossible-to-commit.md)
- [Let references outlive payload bytes](0134-let-references-outlive-payload-bytes-without-a-foreign-key.md)
