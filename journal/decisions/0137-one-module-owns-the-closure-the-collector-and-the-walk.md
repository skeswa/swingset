# D-0137: One module owns the file closure, the collector, and the retention walk

Status: Proposed  
Recorded: 2026-09-18  
Accepted: —  
Acceptance source: —  
Topic: State retention  
Supersedes: —  
Superseded by: [D-0147](0147-only-a-planned-locked-apply-removes-anything.md), for the
collector only: `garbage_collect` is deleted rather than kept as it was. The
closure, the walk, and the one module that owns them are unchanged.

## Decision

`src/swingset/state/retention.py` owns the artifact file closure, the candidate
and artifact collector, and the reachability walk over derivation history. It
is one module, not a package, because all three read the same state and produce
one plan.

`backup/checkpoint.py` keeps the names it had. `_artifact_closure` and
`_referenced_candidates` are imports from the new module, and `CheckpointError`
is now another name for `RetentionError`. (`garbage_collect` was a third import
until D-0147 deleted it.) Existing
callers, messages, and the test that replaces `_artifact_closure` to prove a
checkpoint reads one point in time all keep working unchanged.

`garbage_collect` moves as it is: it still removes directly, with no plan and no
receipt. Step 3b replaces it with `gc --apply`, which removes only what a
written plan names under the writer and control locks. Until that exists, `gc`
behaves exactly as before except that its one-day age floor is now a policy
value.

That last paragraph no longer holds. Step 3b landed in the same change, and
[D-0147](0147-only-a-planned-locked-apply-removes-anything.md) deletes
`garbage_collect` instead of keeping it: there is no direct collector to fall
back on, and `gc` with no flag removes nothing. The rest of this record stands.

## Why

The plan's step 3 asks for one walk that decides both what a checkpoint carries
and what retention keeps. Those were the same question already: the collector
removes what the closure does not reach, and a checkpoint copies what it does.
Leaving the closure in the backup package would mean a state module importing a
backup module to answer a question about state.

One error class follows from one closure. A separate `RetentionError` raised out
of the same function would have made `except CheckpointError` miss it, so the
old name became an alias rather than a parallel class.

Retiring direct removal in the same change would have been two risky changes at
once: the planner is new and unproved, and the collector is the only thing
keeping the worker's disk bounded today (D-0133).

## Alternatives

- Leave the closure in `backup/checkpoint.py` and import it from retention.
  Rejected: state would depend on backup, and a future backup change could
  silently change what retention keeps.
- A `state/retention/` package with a module per concern. Rejected: the three
  read the same rows and feed one plan, and splitting them would hide that they
  share one walk. The file is about a thousand lines, half of it explanation;
  step 3b may split it once apply and reclaim are in it.
- Delete `garbage_collect` now and make `gc` plan-only. Rejected: nothing
  applies a plan yet, so the disk would grow until step 3b lands.

## Consequences

`swingset.backup.checkpoint.CheckpointError` catches retention errors as well,
which is correct but wider than the name suggests; it was not renamed, and
renaming it is still open. Two collectors existed in name only: the plan already
listed what the collector would remove, so replacing it with `gc --apply` was a
change of mechanism, not of policy. That replacement happened in the same change
([D-0147](0147-only-a-planned-locked-apply-removes-anything.md)), with one
change of policy that record states: a file nothing declares is now kept and
reported instead of removed on age.

## Links

- [Implementation plan](../../docs/plans/bounded-state-and-archive.md)
- [D-0119](0119-bound-state-by-interning-and-one-closure.md)
- [D-0147](0147-only-a-planned-locked-apply-removes-anything.md)
- [State contract](../../docs/reference/state.md)
