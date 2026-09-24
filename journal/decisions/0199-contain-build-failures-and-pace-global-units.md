# D-0199: Contain build failures and pace the global units

Recorded: 2026-09-24  
Decided by: agent, 2026-09-24, under D-0183  
Topic: Build  
Supersedes: the "reported, not fatal" health reading of [D-0187](0187-review-dismissals-year-scoped-aliases-and-non-fatal-builds.md)  
Superseded by: —

## Decision

- A release withholds an event whose rows would leave a dangling reference.
  It no longer fails the whole release. The withheld event is listed in
  `row_omissions` with reason `dangling_reference`. Row closure also resolves
  a row's event through rejected parents.
- A build retains its private closure proof only after the candidate files
  are durable. A build generation's recipe names the proof by digest instead
  of embedding it.
- A cycle with a build error reports `failed=true`. The build stays
  non-fatal, so offline work continues. A closure that fails validation,
  or a completion that overruns its write bound, counts as a build error;
  either used to escape and abort the whole cycle, fetching included.
- Local-page evidence reads get their own 30-second window. The window used
  to start before the ~24-second closure walk, so the same build passed or
  failed with machine load.
- A build is admitted at most once per `build_interval_seconds`. The history
  projection is re-materialized at most once per `history_interval_seconds`.
  Production sets 6 and 12 hours.

## Why

One Budafest 2020 callback mark escaped its event's omission because its
contest and entry were both rejected. It failed every build from 2026-09-21
16:34 UTC to the pause. Each failed attempt stored a new 235,394,776-byte proof,
two attempts per cycle, 471 MB a cycle; unnamed proofs reached 33.9 GB.
96 of the 100 cycles that hit the error reported `failed=false`. Each new history generation writes ~200k row
references and re-queues all 2,932 link units, about five hours of work. Fetched
sheets re-queue history constantly, so unpaced history would never settle.

## Links

- [Stability plan](../../docs/plans/stability.md)
