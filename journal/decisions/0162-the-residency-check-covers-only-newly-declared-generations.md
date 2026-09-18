# D-0162: The residency check covers only newly declared generations

Status: Proposed  
Recorded: 2026-09-18  
Accepted: —  
Acceptance source: —  
Topic: State retention  
Supersedes: —  
Superseded by: —

## Decision

When `replace_findings` writes a batch of findings, the residency check runs on
the generations that batch newly declares: for each finding, what it declares
minus what it already has recorded. A generation an unchanged finding already
declares is not checked again.

`hold add` is unchanged: a hold is new every time, so everything it names is
new.

## Why

Plan section 7 admits a new root by checking that root's closure. A generation a
finding already declares is already a root and was checked when it was declared;
the check is what makes a new claim safe, not a recurring audit.

Checking the whole batch is harmless today, because nothing is archived yet. It
stops being harmless the moment step 4 archives a payload: `replace_findings`
replaces every open finding for one owner at once, so one already-archived
generation named by one unchanged finding would raise and abort every other
finding for that owner, including new ones about entirely different subjects,
until somebody restored data nobody was asking for. Findings are how the
pipeline reports what is wrong, so the failure would be that the pipeline stops
being able to say what is wrong.

An archived generation that an open finding still declares is not lost sight of:
it stays a root, so the planner keeps naming it, and doctor reports it.

## Alternatives

- Check the whole desired set. Rejected above: it turns one archived generation
  into an outage of one owner's findings.
- Check the whole set but only when the batch actually changed a declaration.
  Rejected: that is the same failure with a narrower trigger, and the trigger is
  "anything about this owner changed", which is common.
- Recheck and repair by restoring automatically. Rejected: nothing restores yet,
  and a write path that pulls data back from an archive is not something a
  finding writer should do inside a caller's transaction.

## Consequences

A finding can keep a declaration on a generation whose bytes have been archived,
without the next write of that owner's findings failing. The plan still reports
that generation as local-by-finding, which is the state the operator has to
resolve, and `gc --restore` is how it is resolved when step 4 exists. Declaring
that same generation afresh, from any finding, is still refused.

## Links

- [Implementation plan](../../docs/plans/bounded-state-and-archive.md)
- [D-0139](0139-findings-declare-the-support-they-rely-on.md)
- [D-0140](0140-a-hold-is-one-checked-file-under-the-control-lock.md)
- [State contract](../../docs/reference/state.md)
