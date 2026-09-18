# D-0146: A release that says it pinned a closure must produce one

Status: Proposed  
Recorded: 2026-09-18  
Accepted: —  
Acceptance source: —  
Topic: State retention  
Supersedes: —  
Superseded by: —

## Decision

Reading the generations a candidate was built from follows its manifest exactly:

- no `_meta/manifest.json`, or one that is not JSON: `RetentionError`;
- `release_policy.mode` that is not `closure`: no generations, which is the
  truth for a correction-only release;
- `mode` of `closure` with no `closure.selected_generations`, or a list that is
  not a list of strings: `RetentionError`.

The baseline and every pending candidate are read this way, and the failure
stops `gc --plan`. Doctor reports it and keeps reporting the rest of the state.

## Why

This is the one root whose inputs cannot be recovered from the database alone.
Every other root names a generation the database still has. A release pins what
its own manifest says it selected, so if that read quietly returns nothing, every
generation a published release was built from moves from `local` to
`archivable`, and nothing else notices. Plan section 4 says a published dataset
can always be rebuilt from the list of everything it was built from, so a silent
empty answer here is the exact failure the safety rules forbid.

The first version returned no generations for any missing key, so a rename of
`selected_generations`, or a build that stopped writing the closure, would have
been invisible. `publish/safety.py` already refuses a candidate whose mode is
`closure` and whose closure is missing, so this is the same rule read from the
same place, and it is checked by a test that builds a baseline and a pending
candidate rather than being assumed.

## Alternatives

- Return no generations whenever anything is missing. Rejected: see above; the
  failure is silent and the loss is unrecoverable.
- Pin every generation whenever a candidate cannot be read. Rejected: it would
  make one broken candidate pin the whole database, and the operator would see
  nothing archivable with no explanation.
- Read the closure from `release_closures` in the database instead. Rejected:
  a candidate keeps its own manifest so a release pins its inputs even when the
  database has moved on, which is the point of reading it there.

## Consequences

A candidate whose manifest is damaged stops the planner until someone looks,
which is the right time to look. Doctor still works, so the failure is visible
without running `gc`. A correction-only release pins no generations, and that is
now stated rather than falling out of a missing key.

## Links

- [Implementation plan](../../docs/plans/bounded-state-and-archive.md)
- [D-0137](0137-one-module-owns-the-closure-the-collector-and-the-walk.md)
- [State contract](../../docs/reference/state.md)
- [Publishing contract](../../docs/reference/publishing.md)
