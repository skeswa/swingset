# D-0250: Corrections rebuild only current-mode baselines, and a revert is a correction

Recorded: 2026-10-12  
Decided by: agent, 2026-10-12, under D-0172; source: the independent check that blocked turning publication back on  
Topic: Publication  
Supersedes: the correction trigger of H10 for baselines of a superseded build mode  
Superseded by: —

## Decision

- Under the `partitioned` or `whole` release path, the release loop builds a
  correction only when the baseline is `partitioned` or `correction_only`. A
  `closure`, `settled` or unsealed baseline waits for the next normal build,
  which the gate checks. The `closure` path keeps the old rule.
- A `correction_only` baseline is corrected again only when the identity
  part of its token, the suppressions file, the admission policies or the
  revocations change. A new source generation alone does not count.
- A correction is admitted as `correction`, not `build`, so it does not
  reset the build interval.
- Rollback is `swingset publish --revert-to <candidate>`: a correction-only
  candidate read from a former published candidate, committed on top of the
  hub head by the normal publisher, so hub and baseline move together.

## Why

The baseline, 138352e, is a closure-mode build. Its closure no longer
validates, so the first `--publish` tick would have republished its own
tables as a correction, past the gate and with no review. Once a correction
was the baseline, its token held the selection digest, which every accepted
source generation moves, so corrections could repeat hourly and starve the
real build. A hand revert on the hub leaves the local baseline behind, and
reconcile then refuses with "public head changed".

## Alternatives and consequences

Sending corrections through the gate would refuse every withdrawal, since a
withdrawal lowers `confirmed_links`. The cost: a withdrawal or revocation that
arrives under a superseded baseline waits for the next normal build that
passes the gate (daily at best). A revert runs today's identity policy, so
it never restores a join the journal has since withdrawn.

## Links

- [Data corrections: rolling back a publication](../../docs/guides/data-corrections.md#rolling-back-a-publication)
