# D-0147: Only a planned, locked apply removes anything

Status: Proposed  
Recorded: 2026-09-18  
Accepted: —  
Acceptance source: —  
Topic: State retention  
Supersedes: [D-0137](0137-one-module-owns-the-closure-the-collector-and-the-walk.md), for the
collector only: that record kept `garbage_collect` removing directly until step
3b existed, and step 3b now exists.  
Superseded by: —

## Decision

`backup.checkpoint.garbage_collect`, which moved into `state/retention.py` with
the walk, is deleted. `swingset gc` with no flag now prints the plan summary and
names the three commands that act on a plan. The only code in the tree that
removes a file is `gc --apply <plan digest>`, in the new module
`state/retention_apply.py`, which runs under the writer lock and the control
lock and writes a note before the first unlink.

Deciding and doing are separate modules. `state/retention.py` walks, measures
and writes the plan; it removes nothing and imports nothing from the new module.
`state/retention_apply.py` reads a plan and acts on it.

One behaviour goes with the old collector on purpose: it deleted files under
`blobs/`, `extracts/` and `inputs/` that nothing declared, on age alone. The
plan calls those unknown, and unknown is never eligible
([D-0143](0143-an-undeclared-file-is-unknown-in-the-plan.md)). Doctor reports
them and a person decides what they are.

## Why

The old collector computed its closure on a fresh, unlocked `sqlite3` connection
and then unlinked files. It held neither lock. `hold add` takes the control lock
and then checks that everything the hold names is present, so a hold could be
written in the window between the closure being computed and the unlink that
removed a file that hold pins. The result is a hold promising a file that is
gone and a later checkpoint refusing with "referenced artifact is missing".
Plan section 4 says removal happens only from a written plan, under the
pipeline's own locks, and that being old never makes something safe to remove.
Keeping the old path alive next to the new one would have left that race in
place for anyone who typed `gc` out of habit.

Dropping the undeclared-file sweep is a real loss of function, and it is the
one the plan asks for. A file nobody can explain is exactly the case where
deleting on a timer is worst: the explanation is missing, not the file.

## Alternatives

- Keep `garbage_collect` and have `gc` with no flag call it. Rejected: that is
  the race, and it is the thing this step exists to remove.
- Keep it but take both locks inside it. Rejected: it would then be a second,
  planless removal path with no note and no receipt, so a crash halfway would
  leave nothing that says what it was doing.
- Put apply and reclaim in `retention.py`. Rejected: that module is already
  1,200 lines and its whole contract is that it removes nothing. A reader who
  wants to know what can delete a file should have one small file to read.
- Make `--plan` the default for the bare command. Rejected: writing a file is a
  side effect, and a bare command with no flag should be the one that does
  least. The summary prints the same digest, so `--plan` is one keystroke away.

## Consequences

Nothing removes a file without a plan digest and both locks, and every removal
leaves a note and a receipt. Unreferenced artifact files now accumulate until
someone explains them, which shows up in doctor's unknown counts and in the
bytes the size cap measures; that pressure is deliberate. `checkpoint.py` no
longer re-exports the collector, and the test that asserted the re-export now
asserts the absence.

## Links

- [Implementation plan, section 7](../../docs/plans/bounded-state-and-archive.md)
- [State contract](../../docs/reference/state.md)
- [Operations reference](../../docs/reference/operations.md)
- [One module owns the closure, the collector and the walk](0137-one-module-owns-the-closure-the-collector-and-the-walk.md)
- [An undeclared file is unknown in the plan](0143-an-undeclared-file-is-unknown-in-the-plan.md)
