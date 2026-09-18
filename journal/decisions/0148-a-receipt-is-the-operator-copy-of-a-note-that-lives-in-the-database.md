# D-0148: A receipt is the operator's copy of a note that lives in the database

Status: Proposed  
Recorded: 2026-09-18  
Accepted: —  
Acceptance source: —  
Topic: State retention  
Supersedes: —  
Superseded by: —

## Decision

The durable record of an apply is a row in `retention_applies` (schema 32),
keyed by the plan digest. Migration 0032 adds it with the files and payloads the
apply planned to remove, when it started, when its files finished, and the
receipt it wrote. The row commits before the first file is unlinked.

The receipt is a JSON file under `state/gc/receipts/`, written after both locks
are released. An apply receipt is named `<plan digest>.json`; a reclaim receipt
is named for when it ran. Receipts are written once: a rerun reads the note and
returns the recorded receipt rather than writing a new file.

A note is permanent and is written once and finished once. A delete trigger
refuses. An update trigger allows only the one transition this step makes,
filling in `files_completed_at` and `receipt_json` on a note that has neither.

An unfinished note left by a crash is closed by the next apply, with its own
receipt naming which apply finished it, rather than being left open and resumed
by every apply after that.

## Why

SQLite can undo a row it deleted. Nothing can undo a file it unlinked. So the
record of what an apply intends to remove has to be committed before the first
removal, or a crash between the two leaves no way to tell what was in flight.
That is the plan's rule: "the note commits before any file goes", and "if it
crashes, the note says which files were planned, and the next apply finishes
them from a fresh plan".

The note is in the database and the receipt is a file, because they answer
different questions. The note has to survive a restore, so it belongs in the one
thing a backup carries. The receipt is what an operator reads and copies into
`journal/evidence/`, so it belongs on disk in a readable form. `state/gc/` is on
the backup exclusion list, so a receipt is not copied into a checkpoint; that is
correct, because the note is.

Returning the recorded receipt on a rerun, rather than rebuilding one, matters
because the state has moved by then: the files are gone, so replanning gives a
different digest and a rebuilt receipt would describe a different world. The
recorded bytes are what actually happened.

The update trigger exists because a note is evidence. Plan section 4 says
evidence is never rewritten to make a failed step look successful, and an
unconstrained `UPDATE` on this table is exactly how that would happen.

Closing a crashed note with its own receipt keeps the table honest in the other
direction. Leaving it open forever would mean every later apply reports resuming
work that finished long ago, and an operator reading the table could not tell an
apply that is stuck from one that was finished by its successor.

## Alternatives

- Keep only the receipt file. Rejected: `state/gc/` is excluded from backups, so
  a restore would carry no record of any removal, and a crash between the
  database commit and the file write would leave nothing at all.
- Keep only the note and print it. Rejected: the plan asks for one small retained
  receipt per apply and per reclaim, and an operator should not have to open
  SQLite to get one.
- Put receipts under `journal/evidence/` directly. Rejected: the state directory
  is on the worker and the repository is not; the operator copies the small ones
  across deliberately, which is what the evidence rules ask for.
- Let a rerun rewrite the receipt with fresh numbers. Rejected: a receipt records
  what happened, not what would happen now.

## Consequences

An apply is recoverable from the database alone, and a restored backup knows
what was removed and when. The schema carries one more table and two triggers.
Receipts accumulate in `state/gc/receipts/`; each is small, but nothing prunes
them yet, unlike the plans directory. A future step that wants to correct a note
cannot: it has to write a new one.

## Links

- [Implementation plan, sections 7 and 11](../../docs/plans/bounded-state-and-archive.md)
- [Schema history](../../docs/reference/schema-history.md)
- [State contract](../../docs/reference/state.md)
- [Operation guide](../../docs/guides/operation.md)
- [Only a planned, locked apply removes anything](0147-only-a-planned-locked-apply-removes-anything.md)
- Renumbered: `retention_applies` is schema 31, migration 0031, not 32, since [D-0167](0167-intern-derivation-payloads-in-the-last-migration.md).
