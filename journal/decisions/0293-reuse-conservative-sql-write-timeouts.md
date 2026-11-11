# D-0293: Reuse conservative SQL write timeouts

Recorded: 2026-11-11  
Decided by: agent, 2026-11-11; source: measured stage 2 validation repair  
Topic: PostgreSQL worker deadlines  
Supersedes: —  
Superseded by: [D-0294](0294-use-a-seventy-five-second-worker-write-deadline.md) for the worker limit only; conservative timeout reuse remains

## Decision

Round remaining SQL write budgets down to 10-millisecond boundaries when
at least one second remains. Below one second, keep integer milliseconds
and the existing one-millisecond minimum. Reuse the server settings only
for the same absolute deadline and rounded budget. Check absolute expiry
before every statement and preserve all transaction, error and configuration
invalidation. The Python alarm and 60-second worker limit remain unchanged.

## Why

A native scoring pipeline profile records 9,466 extra timeout-setting SQL
commands and 1.120 cumulative seconds in those calls. Reusing a conservative
bound removes redundant round trips. It can cancel a statement less than
10 milliseconds earlier, never later than the sampled remaining budget.
This small measured target does not establish a core timing-budget fix.

## Links

[Stage 2 measurement and validation](../../docs/plans/postgres-migration/7-stage-2.md).
