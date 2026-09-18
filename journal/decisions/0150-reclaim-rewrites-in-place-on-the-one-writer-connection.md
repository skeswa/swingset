# D-0150: Reclaim rewrites the file in place, on the one writer connection

Recorded: 2026-09-18  
Decided by: agent  
Topic: State retention  
Supersedes: —  
Superseded by: —

## Decision

`gc --reclaim` runs `VACUUM` on the connection that already holds the writer
lock. No `VACUUM INTO`, no second database file, no swap and no second install
path. In order: read the usage numbers, check free disk and refuse if it is short,
take the control lock, `PRAGMA wal_checkpoint(TRUNCATE)`, `VACUUM`,
`PRAGMA integrity_check`, `PRAGMA foreign_key_check`, the same schema check a
checkpoint is verified with, `wal_checkpoint(TRUNCATE)` again, release, write one
receipt with all four numbers before and after.

The free-disk floor is twice the current file size, read with
`shutil.disk_usage` on the state directory. The refusal happens before the locks
are taken and before anything is written.

A `VACUUM` that fails with a lock or busy error is reported as a pending write on
another connection, and reclaim stops with nothing changed. Any other SQLite
error is reported as a rewrite that did not finish and rolled back. Automatic
vacuuming stays off.

## Why

The plan's section 7 sets out these five steps, and the reasons hold up against
the alternatives. SQLite's own journal makes `VACUUM` all-or-nothing: a crash or
a kill at any point rolls back to the original file the next time it is opened,
which a test proves by killing a subprocess part way through a measured rewrite
and then checking integrity, the file size and the free list. Readers in
write-ahead-log mode keep reading a consistent view throughout, which another
test proves with an open read-only connection across the rewrite.

Doing it on the existing writer connection is what makes the lock story simple.
The writer lock is already held by the command; adding the control lock gives the
same pair apply uses, in the same order, so no hold or candidate can arrive
mid-rewrite. A second connection would need its own lock story and could deadlock
against the first.

The checkpoint before the rewrite is the same thing backup creation already does,
and for the same reason: start from a file with no pending changes. The one after
it is because truncating the log is what stops the old size being held in the log
instead of the file.

The free-disk floor is a guess with a reason. SQLite builds the rewritten
database as a temporary file and journals the rewrite, so the peak is roughly the
old file plus the new one. Twice the current size is the plan's number and is
generous when the rewrite shrinks the file, which is the case reclaim exists for.
Refusing before the locks means a short volume costs nothing and blocks nobody.

Branching on the error text is unpleasant and is the only signal SQLite gives.
The distinction matters to an operator: a pending write is something to go and
look at, while an interrupted rewrite is something to run again.

## Alternatives

- `VACUUM INTO` a new file and swap it in. Rejected: the swap is the dangerous
  part. It needs a second install path, and a crash between write and rename
  leaves two files where the recovery rule has to decide which is real. In-place
  has one file and one rule.
- Vacuum on a second connection. Rejected: it would contend with the writer
  connection this command already holds, for no gain.
- Turn on `auto_vacuum`. Rejected: it changes the page layout and needs a full
  `VACUUM` to turn on anyway, so it cannot be the way out of the first oversized
  file. The plan says it stays off.
- Skip the free-disk check and let SQLite fail. Rejected: it fails part way,
  after taking both locks and stalling the pipeline, and the plan says refuse
  before touching anything.
- Run reclaim automatically after every apply. Rejected: it holds both locks for
  the whole rewrite, which on a large database is minutes. When to spend that is
  an operator's call, and the apply receipt gives them the free-list number to
  make it with.

## Consequences

The file can be brought back under the size cap, so the cap becomes a limit the
pipeline can be returned under rather than one that can only be raised. The cost
is both locks held for the length of a full rewrite and a free-disk requirement
of about twice the file size, which the plan's section 9 says the cap has to be
set with in mind. Reclaim needs the writer lock, so it cannot run while a cycle
is running.

## Links

- [Implementation plan, sections 7 and 9](../../docs/plans/bounded-state-and-archive.md)
- [State contract](../../docs/reference/state.md)
- [Operation guide](../../docs/guides/operation.md)
- [Two retention limits and the collector age become policy](0141-two-retention-limits-and-the-collector-age-become-policy.md)
- [The size cap sets one whole-pipeline pause](0142-the-size-cap-sets-one-whole-pipeline-pause-and-never-rewrites-one.md)
