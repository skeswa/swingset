# D-0145: A hold file is read strictly, and only a file named like a hold is read as one

Recorded: 2026-09-18  
Decided by: agent  
Topic: State retention  
Supersedes: —  
Superseded by: —

## Decision

`retention.holds` reads only `state/holds/hold_*.json`, which is the name
`add_hold` writes. Any other file in that directory is left alone.

A file that is named like a hold and cannot be read is a `RetentionError` saying
which file it is and to repair or remove it. That covers bad JSON, a record that
is not an object, a wrong `format`, and a `hold_id` that does not match the file
name. A raw JSON decoding failure never escapes.

Doctor catches that error, prints it, and reports the rest of the state.

## Why

The hold reader was called from the file closure, which every checkpoint and the
collector use, and it parsed every `*.json` in the directory with no guard. An
operator's own note saved as `state/holds/notes.json`, or a hold file truncated
by a full disk, made `create_checkpoint`, `garbage_collect`, `gc --plan` and
`doctor` all fail with a `json.JSONDecodeError` and no remedy, confirmed by
experiment. Doctor exited 1 with no output at all, which its own docstring says
must not happen.

Reading only `hold_*.json` removes the most likely cause, an operator's file
that was never a hold, without weakening anything. A truncated hold still stops
the planner, and that is right: a hold says what a restore with no network must
find, and a backup that quietly left out half of one would be a promise the
backup could not keep. Failing closed on a real hold and ignoring a file that
was never one are different answers to different questions.

## Alternatives

- Skip unreadable holds and carry on. Rejected: the backup would silently omit
  held files, and `gc --apply` would plan against holds it could not see.
- Keep reading every `*.json`. Rejected: it makes any file an operator leaves in
  the directory a pipeline-wide failure.
- Move holds into the database. Rejected by D-0140: a hold has to be placed
  under the control lock while a cycle holds the writer lock, and has to be
  readable when the database will not open.

## Consequences

An operator can keep notes next to their holds. A damaged hold is loud, with the
file name and what to do, and doctor still reports everything else. A future
hold format will need its own name or its own version field inside the record.

## Links

- [Implementation plan](../../docs/plans/bounded-state-and-archive.md)
- [D-0140](0140-a-hold-is-one-checked-file-under-the-control-lock.md)
- [Operations contract](../../docs/reference/operations.md)
