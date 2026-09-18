# D-0159: An expired pause is not an existing pause

Status: Proposed  
Recorded: 2026-09-18  
Accepted: —  
Acceptance source: —  
Topic: State retention  
Supersedes: —  
Superseded by: —

## Decision

When the size cap checks whether a whole-pipeline pause already stands,
`enforce_size_cap` asks the question controls asks: a row on `all`/`all` whose
`until_at` has passed is not a pause. It reads
`WHERE scope_kind='all' AND scope_id='all' AND (until_at IS NULL OR until_at>?)`
with the current time in the format `controls` writes, so the two agree. If only
an expired row is there, the cap records its own pause, and `change_control`
expires the old row in the same transaction that writes the new one.

## Why

`matching_pauses` skips a row whose moment has passed, and `expire` deletes it
only at a `change_control` or admission boundary. Between the expiry time and
the next such boundary the row sits in the table holding nothing. Reading the
row itself as an existing pause therefore left the worker running over its size
cap with nothing paused, and told the operator there was already a pause with
somebody else's reason on it. The cap's own rule, never to rewrite a standing
pause ([D-0142](0142-the-size-cap-sets-one-whole-pipeline-pause-and-never-rewrites-one.md)),
is about words an operator is still standing behind, not about a row that has
run out.

## Alternatives

- Expire the row here first. Rejected: expiry is a mutation and writes a control
  event, and the cap reads a read-only copy of the database before it decides to
  write anything. `change_control` already expires it when the cap writes.
- Read the row and compare in Python. Rejected only on cost: it is the same
  rule, and the format is `controls`' own, so the comparison belongs in the
  query next to it.

## Consequences

An operator who pauses everything until a time and lets it lapse gets the cap's
pause when the file is over the cap, which is what the cap is for. One more
control event is recorded at that point, the expiry of the operator's pause, so
the history says why the reason changed.

## Links

- [Implementation plan](../../docs/plans/bounded-state-and-archive.md)
- [D-0142](0142-the-size-cap-sets-one-whole-pipeline-pause-and-never-rewrites-one.md)
- [Operations contract](../../docs/reference/operations.md)
- [State contract](../../docs/reference/state.md)
