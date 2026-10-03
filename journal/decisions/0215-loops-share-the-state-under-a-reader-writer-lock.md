# D-0215: Loops share the state under a reader-writer lock

Recorded: 2026-09-27  
Decided by: agent, 2026-09-27, under D-0175; source: phase 2 of the [architecture plan](../../docs/plans/architecture.md) (findings F5, F6, F7, F10)  
Topic: Local state  
Supersedes: —  
Superseded by: in part, [D-0239](0239-the-loops-are-the-only-orchestration.md) (the cycle is gone; the loops are the only service)

## Decision

`state.lock` becomes a reader-writer lock with an intent file in front of it.
Every writer that holds the state alone today (the cycle, CLI mutations,
backup, restore, `gc`, `accept-year`) keeps exclusive: it takes `state.intent`
then `state.lock`, both exclusive, and is the only mode that migrates. A loop
takes its own `loop-<name>.lock`, then both files shared and non-blocking, and
drops the intent; if anything is held it skips its tick. An older schema makes
a loop hand off to one exclusive open that migrates. `heavy.intent` and
`heavy.lock` follow the same pattern: while release builds, derive starts no
unit at all.

SQLite's writer is taken before the write bound starts: `BEGIN IMMEDIATE`
waits up to 70 s (busy timeout) on every writable connection, the
publication's included; the wait is counted per process, and only the body
is bounded. An admission waits 90 s for `control.lock`. Either timeout skips
the unit or request and counts nothing.

Each table has one writing loop. The exceptions are rows each loop keeps
for itself (`runs`, `loop_state`, its `guard_reasons` subjects), `watches`
(acquire, and derive through parse admission and link effects) and
`cursors` (acquire and derive for the registry sweep, plan for enumeration
cursors). An active
publication fences every non-control table, so it is a pause for the other
loops: a tick checks for one before a unit or request starts. A unit write
that still meets the fence writes nothing more, since the queue and attempt
tables are fenced too; the loop's next tick after the publication closes the
attempt, uncounted, to retry in 60 s (F6). A tick whose start meets the fence
skips. The cycle stays the service until
`services.swingset.loops.enable` is set; both use the same lock.

## Why

One process held `state.lock` exclusive for its whole life, so five loops could
not coexist (F5). The intent keeps a waiting writer, such as the daily backup,
from being starved by loops that re-take the shared lock. With several writers,
a bound that included the lock wait would fail short writes that only queued
behind another process's (F7). A build and a large project unit together pass
the worker's 8 GB (F10); on the Mac even a parse-only derive tick beside a
build reached 8.2 to 9.2 GB, so derive waits whole.

## Alternatives and consequences

A lock held only around each transaction would let loops interleave, but then
a CLI mutation could not run between two related transactions of the cycle.
Loops pause while any exclusive command waits or runs, including backup, and
while a publication uploads. Narrowing the fence is phase 3.

## Links

- [State: locks](../../docs/reference/state.md#locks)
