# D-0213: Keep no bookkeeping a committed run does not need

Recorded: 2026-09-26  
Decided by: agent, 2026-09-26, under D-0175 and D-0183; source: the operator's resolution of phase 1's growth target in the [architecture plan](../../docs/plans/architecture.md)  
Topic: Local state  
Supersedes: —  
Superseded by: —

## Decision

When a parse, project or link unit commits its output, the same transaction
deletes its execution admission and that admission's `execution_dependencies`
rows. A success whose work-attempt requirement has no finding writes no
`requirement_attempts` row. The `work_attempts` row stays: it holds the run,
times, fingerprint and outcome. Failures, holds, supersessions and every
other action kind keep all three as before.

## Why

Only an unsettled admission is ever read: pause drains, `recover_admissions`,
the publication fence and `control status`. A settled one repeats its work
attempt. The 16:10 copy held 746,339 admissions with 5.8 million dependency
rows, about 1.5 GB with indexes, and each unit run added about 2.9 KB of
bookkeeping, 104 of the 170 MB a code-only deploy grew in R7. A finding's
`attempt_count` no longer counts successes from before the finding existed.

## Alternatives and consequences

Trimming the rows already written deletes production data and needs the
independent check; it is left to phase 2's `work_queue` and phase 4's sweep.

## Links

- [Local state](../../docs/reference/state.md)
