# D-0195: Answer the projection question once per pass

Recorded: 2026-09-23  
Decided by: agent, 2026-09-23, under D-0183  
Topic: Acquisition  
Supersedes: extends [D-0181](0181-answer-the-year-gate-once-per-offers-pass.md)  
Superseded by: —

## Decision

Whether an event-list projection is pending is answered once per pass, not
once per year or per page. The offers pass, an allocated dispatch and the
event-site review each open a context in which every call path that asks
the question gets the first answer. An allocated dispatch also evaluates
only the page whose watch it was allocated, instead of every page of the
plan inside its bounded write.

## Why

Accepting all seventeen years on 2026-09-23 opened phase two, and the first
cycle after it failed with `WriteDeadlineExceeded`: the dispatch step
re-evaluated all 6,342 plan pages inside one 45-second write, and each page
asked the projection question, which walks every pending derivation and
takes eight seconds on the 35 GB database. D-0181 had memoized the year gate
in the offers pass but not in dispatch, and the walk itself still ran once
per year. Reproduced on a scratch copy: the offers pass took 98 seconds, 73
of them in nine walks. With the fix it takes one walk. Without the fix every
cycle fails at the start of acquisition and collection stops again.

## Links

- [Receipt](../evidence/runtime/v5-year-acceptance-2026-09-22/receipt.json)
- [Backfill reference](../../docs/reference/backfill.md)
