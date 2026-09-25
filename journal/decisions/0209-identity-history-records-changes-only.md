# D-0209: Identity history records changes only

Recorded: 2026-09-26  
Decided by: agent, 2026-09-26, under D-0175; source: phase 1 review of the [architecture plan](../../docs/plans/architecture.md)  
Topic: Identity  
Supersedes: —  
Superseded by: —

## Decision

A link run leaves a subject's rows alone when nothing about it changed: the
`identity_links` row, its `link_candidates`, its `identity_link_resolutions`
row and the newest `identity_link_history` row must all equal what the run
would write, `asserted_at` and `run_id` aside. Such a subject keeps its rows,
so no `superseded` and `accepted` pair is appended to its history. A subject
whose conclusion, candidates, resolution or evidence changed is rewritten as
before (`link/persistence._replace_resolutions`).

History now holds one row pair per change, not one per run. A kept row's
`asserted_at` and `run_id` name the run that last changed it, which is what
a derivation cutoff already publishes (D-0205).

## Why

The phase 1 rehearsal (R5) re-ran all 2,933 links after a code-only change.
Every link kept its generation, yet the writer deleted and re-inserted
every link row, and the delete trigger plus `retain_resolution` appended
288,836 history rows, about 208 MB of the 415 MB the deploy grew. A replay
epoch or a history re-run repeats that outside deploys. Nothing reads the
history but people; a row pair that repeats the previous one tells them
nothing. The same drain with this rule (R7) added 0.05 MB of history.

## Links

- [Identity linking](../../docs/reference/identity-linking.md)
