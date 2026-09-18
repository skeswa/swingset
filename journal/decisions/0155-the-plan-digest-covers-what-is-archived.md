# D-0155: The plan digest covers what is archived

Recorded: 2026-09-18  
Decided by: agent  
Topic: State retention  
Supersedes: —  
Superseded by: —

## Decision

The retention plan records, for every generation, whether it is archived. The
planner reads the `archived_generations` table of plan step 4 once, writes
`"archived": true` or `false` into each generation row, and counts the archived
ones in the plan's totals. `gc --apply` reads that flag from the plan it was
given, not from the database, when it works out which payload bytes may go.

So payload removal now depends only on what the reviewed plan says. Archiving a
generation changes the plan, changes its digest, and stops an apply of the old
digest.

## Why

Plan section 11 says that before the first removal of row data on production
"the exact plan has been reviewed and its fingerprint written down". Apply
already stops when the fresh plan's digest differs from the one it was given.
That check is only worth what the digest covers.

Nothing in the plan said whether a generation was archived, but eligibility for
payload removal read `archived_generations` live at apply time. A row inserted
between `gc --plan` and `gc --apply` therefore left the digest unchanged, passed
the check, and widened what the apply removed beyond what the operator read.
The review gate would have been passed by a plan that no longer described the
removal.

Today this is inert: the table does not exist, so nothing is archived and no
payload is eligible ([D-0149](0149-no-generation-is-eligible-until-a-table-says-it-is-archived.md)).
Step 4 is what turns the path on, and the fence has to be right before then, not
after. The cost is one query per plan.

The same rule already holds for the other input the planner cannot recompute
from the database: the collector's age floor is read from each directory and
written into the plan, so apply compares it against its own clock rather than
re-reading policy.

## Alternatives

- Read `archived_generations` live in the eligibility check. Rejected: that is
  what let a removal be wider than its reviewed plan.
- Keep the flag out of the plan and have apply compare a second digest of the
  archive table. Rejected: two digests to review, and the operator's copy of the
  plan still would not say which generations were archived.
- Put only a list of archived generation ids at the top of the plan. Rejected:
  the per-generation flag reads next to the `list` and `why` that explain the
  same generation, and the totals give the summary.

## Consequences

Every plan digest changes when anything is archived, so an archive run always
forces a fresh plan and a fresh review before any byte goes. Plan files grow by
one boolean per generation. Doctor's totals gain `archived_generations`, which
is zero until step 4 exists and is the number an operator checks before an apply
that would remove row data. `eligible_payloads` no longer queries the archive
table itself; step 4 adds its two-checked-copies condition to the planner, where
the digest will cover that too.

## Links

- [Implementation plan, sections 7, 8 and 11](../../docs/plans/bounded-state-and-archive.md)
- [State contract](../../docs/reference/state.md)
- [No generation is eligible until a table says it is archived](0149-no-generation-is-eligible-until-a-table-says-it-is-archived.md)
- [Only a planned, locked apply removes anything](0147-only-a-planned-locked-apply-removes-anything.md)
