# D-0138: Recovery markers pin the current pointers and nothing more

Status: Proposed  
Recorded: 2026-09-18  
Accepted: —  
Acceptance source: —  
Topic: State retention  
Supersedes: —  
Superseded by: —

## Decision

`RESTORE_PENDING` and the `operator-hold` file are retention roots that pin
exactly the current scope pointers and the generations the baseline and any
pending candidate name. They add nothing else. Both are already roots in their
own right, so in practice these two markers change no plan; the walk lists them
so a plan says out loud that an interrupted restore or a paused operator keeps
everything current.

Neither marker records which computation it is about. `RESTORE_PENDING` is one
line of text saying verification is pending, and `operator-hold` is a flag the
scheduler reads. An operator who needs more than the current pointers kept
writes an explicit hold under `state/holds/`, which does name what it is about
and is checked when it is written.

## Why

The safety rules say recovery markers keep everything they need, including
everything those things were built from. What an interrupted restore needs is
the state it just installed: the pointers, the baseline, and the pending
candidate. That is what the restore path verifies before it removes the marker.

Claiming more would be inventing a fact. There is no honest reading of a flag
file that says which older generation someone was investigating, and guessing
wide (for example, keeping every generation while a marker exists) would make
the marker a way to switch retention off by accident.

## Alternatives

- Treat either marker as pinning all history. Rejected: an operator hold can
  stand for weeks, so this would make the cap unreachable and quietly disable
  the plan.
- Leave both markers out of the roots. Rejected: the plan would not record why
  the current pointers are kept during a restore, and a later change to pointer
  roots could remove that protection without anyone noticing.
- Extend the marker files to record generations. Rejected: that is what a hold
  is, and a second format for the same thing would drift.

## Consequences

An operator who wants older data kept during an investigation has to say so with
a hold, and the hold is refused if that data is not local. Plans taken during a
restore or an operator hold are the same as plans taken outside one, which makes
them easy to compare.

## Links

- [Implementation plan](../../docs/plans/bounded-state-and-archive.md)
- [Operations contract](../../docs/reference/operations.md)
- [Recovery](../../docs/reference/recovery.md)
