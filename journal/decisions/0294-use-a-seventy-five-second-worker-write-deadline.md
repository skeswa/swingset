# D-0294: Use a 75-second worker write deadline

Recorded: 2026-11-13  
Decided by: owner, 2026-11-13; source: “let's make the boundary 75 seconds then”  
Topic: Worker write deadline  
Supersedes: the 60-second limit in [D-0217](0217-large-generations-store-in-chunks-and-the-write-deadline-is-an-alarm.md) and its unchanged-limit reference in [D-0293](0293-reuse-conservative-sql-write-timeouts.md)  
Superseded by: —

## Decision

Every worker write has one 75-second absolute deadline. Nested writes and
commit acknowledgement inherit it. Cancellation, rollback, uncertain-commit
handling, overrun alarms and retry rules keep their existing behavior.
No unit gets a separate allowance.

Future Stage 2 diagnostics use the same 75-second policy. The frozen producer
keeps its verified files; the operator explicitly applies the new limit only
to its owned copy, records the installed and applied limits, and restores the
installed setting on exit. Old 60-second receipts remain historical.

## Why

The owner chose 15 seconds more headroom after one acceptance attempt completed
in 59.13 seconds and another was stopped at 60.59 seconds. The latter does not
establish its full completion time. The new boundary must be tested in a fresh
rehearsal; it does not establish a performance fix or migration success.

## Alternatives and consequences

Keeping 60 seconds would preserve the earlier alarm sensitivity. The new limit
can hold a failed writer for 15 seconds longer. The existing 90-second admission
wait still exceeds it; manual controls can time out at their 60-second default.
Chunked storage and the requirement to keep writes small still apply.

## Links

[State reference](../../docs/reference/state.md)
