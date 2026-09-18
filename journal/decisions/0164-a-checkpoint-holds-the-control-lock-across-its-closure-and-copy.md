# D-0164: A checkpoint holds the control lock across its closure and copy

Recorded: 2026-09-18  
Decided by: agent  
Topic: State retention  
Supersedes: —  
Superseded by: —

## Decision

`create_checkpoint` takes the control lock around two things: computing the file
closure, and copying the files it names. It takes it after copying the database,
and after the writer lock the backup command already owns, in the documented
order state.lock, then control.lock, then SQLite, and never while a SQLite write
transaction is open. `control_timeout` is a parameter with the same default as
every other control wait.

A caller that already fenced a wider operation with the control lock keeps its
own fence, and the checkpoint takes nothing. `control_lock` stays exactly as
non-re-entrant as it was, because two control mutations by one thread are two
mutations and the second must wait like anyone else. It only records which
thread is inside it, and `holds_control_lock(state_dir)` answers the one
question this needs. Operator tools such as the held schema-28 checkpoint helper
fence a whole operation that way, and they must keep working.

## Why

A hold is one file written under the control lock after its contents are
checked, and a held digest joins the file closure
([D-0140](0140-a-hold-is-one-checked-file-under-the-control-lock.md)). The
checkpoint computed its closure and copied under the writer lock alone, so a
hold could be committed in between. The checkpoint would then carry the hold
file and not the file it holds, because the hold file rides in the copy as
ordinary state while its digest was resolved before the hold existed.

Nothing would have reported that gap. Files a hold names are filtered by
`is_file()` on purpose, because a hold names a digest without saying which of
`blobs/` or `extracts/` it is in, so a digest that resolves to neither is simply
absent from the closure rather than an error. `verify_checkpoint` checks that
the copy matches its own manifest, not that every hold inside it is satisfied.
The restored state would have held something that was not there, which is
exactly the promise a hold exists to make.

`gc --apply` already takes both locks for the same reason, so the removal side
was closed and the backup side was not.

## Alternatives

- Validate holds in `verify_checkpoint` instead. Rejected: it turns a race into
  a failed backup rather than preventing it, and the plan's safety rules say the
  backup path is the one thing that has to keep working when the state directory
  is in a bad way.
- Recompute the closure after the copy and copy the difference. Rejected: it
  never terminates in principle, and a hold placed during the second pass has
  the same problem.
- Take the control lock for the whole of `create_checkpoint`, database copy
  included. Rejected: the database copy is the long part, and holding a control
  mutex across it would block admission and every control command for the
  duration for no benefit; the closure resolves from the copied database, which
  is already a fixed point in time.
- Make `control_lock` re-entrant for the thread that holds it. Rejected: it
  would also let a control change commit inside an operation that fenced
  controls precisely to stop that, which is the guarantee
  `test_control_mutex_fences_lifecycle_replacement` enshrines. Asking whether
  this thread already holds it leaves that guarantee alone.

## Consequences

`hold add`, `hold remove`, control changes and candidate admission wait while a
checkpoint copies files, and a waiter that runs out of time gets the ordinary
`ControlTimeout`, which says nothing was persisted. The wait is the copy, not
the database backup. A checkpoint now creates `control.lock` in the state
directory if it is not there; that name is already excluded from the copy, so no
checkpoint carries it.

`control_lock` now keeps a small record of which thread is inside it. That
record is per process, so it says nothing about another process; it is only ever
read to answer "am I already inside this one", and every other question is still
answered by the file lock itself.

## Links

- [Implementation plan](../../docs/plans/bounded-state-and-archive.md)
- [D-0140](0140-a-hold-is-one-checked-file-under-the-control-lock.md)
- [D-0147](0147-only-a-planned-locked-apply-removes-anything.md)
- [State contract](../../docs/reference/state.md)
- [Backup and restore guide](../../docs/guides/backup-and-restore.md)
