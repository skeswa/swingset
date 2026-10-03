# D-0238: Leased units carry no admission

Recorded: 2026-10-03  
Decided by: agent, 2026-10-03, under D-0172; source: architecture plan section 8, phase 5 (owner-adopted 2026-09-25)  
Topic: Local state  
Supersedes: the parts of H13 ([state](../../docs/reference/state.md)) and [D-0213](0213-keep-no-bookkeeping-a-committed-run-does-not-need.md) that give parse, project and link units an execution admission; narrows section 8's "drop the admission ledger and `recover_admissions`"  
Superseded by: in part, [D-0239](0239-the-loops-are-the-only-orchestration.md) (the locked CLI commands keep `recover_admissions` after 5b)

## Decision

A parse, project or link unit writes no `execution_admissions` or
`execution_dependencies` row and takes no control lock. One
`BEGIN IMMEDIATE` checks its pauses (`matching_pauses(for_unit)`), captures
its inputs and begins its attempt. The `work_queue` lease and the running
`work_attempts` row are its in-flight record; lease takeover and a loop's
dead-run recovery close it. `controls.status()` and `pause --wait` count
leased units as draining. `admission()` refuses `parse`, `link`, `work_` ids
and a work attempt.

The ledger keeps actions with no lease: requests, fetches, CDX and stage
operations, registry crosschecks, builds and publications. The maintain loop
deletes settled rows older than 30 days, 5,000 per transaction, except
publications, the newest build and the newest completed build, and rows
`scheduler_requests` or `history_origin_requests` names.
`scheduler_requests` has no collector, so request rows outlive the prune:
the ledger grows by about 1,800 request rows a day until that table gets
one. Maintenance item 15 holds it; the watchdog's `[admissions-growth]`
fires when maintain's `admissions_rows` passes 200,000. `recover_admissions`
stays only as the exclusive-open safety of migrate and restore (and the
cycle and unit commands until 5b). Migration 37 (part 2) deletes every
`work_` row and its scope rows, copying the kept scope rows into a new
table.

## Why

Units were 902,488 of 946,000 ledger rows and nearly all of 6.9 million
scope rows on the 09-29 copy, about 1.7 GB, plus one control-lock wait per
unit. A pause commits with `BEGIN IMMEDIATE` on the same file, so a unit
either sees it or is leased before it and drains. Section 8 also wanted the
ledger gone, but requests, CDX passes and builds have no lease, and the
publication fence triggers test the publication row, which must stay
`uncertain` until its receipt is reconciled.

## Alternatives and consequences

A pause's drain now depends on lease liveness: a dead process's lease shows
as draining until it expires (30 minutes); the drain row names
`lease_until`. An expired lease is listed under `dead_leases` and does not
hold `pause --wait`, since only takeover, not time, ends it. With the
cycle gone, every loop tick also settles an active non-publication
admission whose run has finished, and after six hours one with no loop run,
so a failed settlement cannot drain a pause forever; the watchdog names one
active over two hours. Of builds, the newest admitted and the newest
completed stay: `build_deferred_for_interval` and `release_lag` read only
those (215 builds were kept on the 09-29 copy before this narrowing).

## Links

- [Architecture plan, phase 5](../../docs/plans/architecture.md)
