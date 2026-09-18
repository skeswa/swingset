# D-0151: A resumed note removes only what the fresh plan still names

Status: Proposed  
Recorded: 2026-09-18  
Accepted: —  
Acceptance source: —  
Topic: State retention  
Supersedes: —  
Superseded by: —

## Decision

An unfinished `retention_applies` note is never replayed on its own. When
`gc --apply` finds a note whose files never went, it finishes only the files the
plan it just recomputed under both locks still calls removable. Anything that
plan now keeps is left where it is and recorded under `skipped_files` in that
note's receipt. The note is then closed, because the next plan names a file
again if it becomes removable again.

The note also carries `removed_payload_bytes`, measured before the payloads go.
A receipt rebuilt from a note reads the payload list and that number out of the
note, never out of state that has moved on. And a receipt file missing next to a
finished note is written from the note the next time that digest is run, because
the file is the operator's copy and the note is the record
([D-0148](0148-a-receipt-is-the-operator-copy-of-a-note-that-lives-in-the-database.md)).

`swingset gc` with no flag hands out no digest to apply. It points at
`gc --plan`, and `gc --plan` prints the apply line next to the file it wrote.

## Why

A note says what was removable when it was written. A crash ends the apply and
releases both locks, and the state moves after that: a rollback can point
`baseline` back at a candidate, a publication can start on one, an operator can
raise `retention.collect_older_than`. Replaying the note then removes a file the
current plan calls local. Two runs of the first draft showed it: with the age
floor raised, a fresh plan with nothing eligible at all still unlinked the
candidate the note named; with `baseline` relinked to that candidate, the apply
left a dangling symlink and the next checkpoint failed on the missing
`PUBLISHED`. The plan's safety rule is that removal happens only from a written
plan of the current state, and a stale note is not one.

Closing a note whose files were skipped is not a licence to forget them. The
plan is recomputed from state on every run, so a file that becomes removable
later is named by the plan that finds it. Leaving the note open instead would
have every later apply reconsider a list that gets more wrong with time.

Storing the payload byte count in the note is the only way a resumed receipt can
state it. The bytes are gone by then, so nothing can measure them again, and a
receipt that said zero would understate a removal that happened.

## Alternatives

- Replay the note as written. Rejected: it removes what no current plan names.
- Replay it, but only for files that are still absent from the plan's local
  list. Rejected: the same check, stated as a double negative, and it would
  still remove a file the plan calls unknown.
- Leave a note with skipped files open. Rejected: every later apply would redo
  the same comparison, and a note that is never closed has no receipt.
- Recompute the payload bytes on resume. Rejected: they are deleted, so the
  answer is always zero.

## Consequences

A crashed apply can leave files behind that its plan intended to remove. That is
the safe direction, and the next plan names them again. Receipts gain
`skipped_files`, so an operator can see the difference between a file that went
and one a later plan protected. `retention_applies` gains one column, which is
part of the same unreleased migration 0032.

## Links

- [Bounded state plan, step 3b](../../docs/plans/bounded-state-and-archive.md)
- [Apply, notes and receipts](../../docs/reference/state.md#apply)
- [Only a planned, locked apply removes anything](0147-only-a-planned-locked-apply-removes-anything.md)
- [A receipt is the operator copy of a note](0148-a-receipt-is-the-operator-copy-of-a-note-that-lives-in-the-database.md)
- Renumbered: the unreleased apply-note migration is 0031, not 0032, since [D-0167](0167-intern-derivation-payloads-in-the-last-migration.md).
