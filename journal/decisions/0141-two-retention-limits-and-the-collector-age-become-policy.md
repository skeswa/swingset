# D-0141: Two retention limits and the collector's age floor become policy

Recorded: 2026-09-18  
Decided by: agent  
Topic: State retention  
Supersedes: —  
Superseded by: —

## Decision

`config/sources.toml` gains a `[retention]` table with three values, parsed into
`Config.retention`. The bundle captures them the way it captures every other
setting in that file, by capturing the file, so a saved bundle explains the cap
and window that were in force:

| Value                | Default       | Meaning                                                          |
| -------------------- | ------------- | ---------------------------------------------------------------- |
| `max_database_bytes` | 8,000,000,000 | The most `state.sqlite` may be on disk                           |
| `recent_window`      | 3             | How many of the newest generations of each scope stay local      |
| `collect_older_than` | 86400         | How old a disposable candidate or artifact must be to be removed |

`max_database_bytes` accepts a plain number or a `KB`/`MB`/`GB` suffix;
`collect_older_than` accepts the usual duration spellings. Doctor reports usage
against the first two. The third replaces the one-day constant in `cli.py`.

They get no accepted-input name of their own. `config/sources.toml` is already
an accepted input, and no stage recipe names it, so changing any of the three
invalidates no derivation and recomputes nothing. A separate name would also
have meant editing the frozen phase-1 resume tool, which mirrors the exact set
of accepted inputs it was reviewed against (D-0013).

## Why

The plan asks for two knobs; the collector's hidden constant is a third value of
exactly the same kind, and leaving it in the code meant the only way to change
how long an orphan waits was to edit and redeploy.

The cap default is set against measurement, not taste. The specimen database is
5,016,920,064 bytes, and reclaiming space needs free disk of about twice the
file size while `VACUUM` runs, so 8 GB leaves the specimen room to grow while
keeping the rewrite inside the worker's disk bound (D-0133). It is deliberately
not a number the current database already exceeds: a cap that pauses the
pipeline on the first cycle after deployment would teach operators to ignore it.

The window default of 3 is a judgment call, and the plan says so. Three keeps
the current generation, the one before it, and one more, which covers the usual
"what changed between the last two runs" question without pinning history.

## Alternatives

- One knob, the cap alone. Rejected: without a window the walk keeps only what
  is currently pointed at, so an investigation started an hour late finds
  nothing.
- Keep the collector's age as a constant. Rejected: it is policy, it is already
  in a plan file, and an operator clearing disk pressure should not need a
  release.
- Put the values in `hosts.toml`. Rejected: that file is per-host fetching
  policy; retention is not about a host.

## Consequences

Three more values an operator can get wrong, all bounded: the parser refuses a
cap below one byte, a window below one, and a negative age. The cap is measured
on the file, not on the rows, so it is unchanged by deletion until `gc --reclaim`
runs; doctor reports both numbers so that is visible rather than confusing.

## Links

- [Implementation plan](../../docs/plans/bounded-state-and-archive.md)
- [State contract](../../docs/reference/state.md)
- [D-0133](0133-bound-the-worker-disk-and-remove-rehearsals-eagerly.md)
- [D-0013](0013-preserve-historical-migration-tests.md)
