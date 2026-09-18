# D-0034: Record bounded observations of known event completion

Recorded: 2026-09-16  
Decided by: agent  
Topic: Event accounting  
Supersedes: —  
Superseded by: D-0037 extends page accounting to supported unavailable-origin gaps; parent support and historical semantics remain unchanged.

## Decision

Add sampled parent support and change-only event accounting receipts in schema 22. Extend the existing progress observer and its shared verification session,
transaction fences, cursor, and H13 execution controls. Reuse the inventory's
parent verifier through a neutral admission helper. There is no new worker or
request loop.

A fresh, nonempty current enumeration is observed locally accounted only when
all listed pages and required parents have positive observations. A definite
missing obligation establishes unfinished work; incomplete evidence remains
unassessed. Reports state the observation window and recheck metadata, without
claiming instantaneous artifact presence or replacing live inventory checks.

Keep successful operation progress separate. Membership changes, repeated checks,
restored files, and unknown intervals are not successful work. Record reopening
only after a definite negative follows a positive assessment of the same
enumeration. Retirement remains explicitly unassessed in this increment.

## Why

Per-page progress alone cannot establish that every known page and its parent
support were checked. A bounded shared verifier avoids duplicate artifact reads
and prevents one catalog sweep from acquiring a new budget for every event.
Current observations expire at their earliest contributing expiry; immutable
receipts preserve history without becoming completion authority.

## Alternatives

- A persisted complete flag could outlive evidence, revocation, or membership
  changes. Derive the sampled classification from fenced observations instead.
- Calling the full inventory once per event would reset budgets and duplicate
  verification. Reuse its parent check inside the existing session.
- Inferring retirement from a smaller enumeration would confuse omission with
  authorized withdrawal. Defer retirement proof to the next increment.

## Consequences

The defaults remain proposed and unmeasured: at most eight events, 32 selected
pages per event, 128 members per enumeration, and one bounded session. Parent
checks follow fresh positive page observations, with priority when parent support
is missing and oldest-parent-first rotation. Oversized evidence remains unknown.

The nested accounting report has catalog pagination and bounded metadata reads.
Counts are page subtotals when coverage is incomplete. It exposes latest and last
definite historical observations; historical reopening totals remain unassessed.
No new eligible-time clock, completion ETA, retirement authority, production
migration, or operating acceptance follows.

## Links

- [Investigation and acceptance matrix](../investigations/2026/event-fleet-accounting-2026-09-16.md)
- [Reporting contract](../../docs/reference/operations.md#event-completion-reporting-h14-extension)
- [Persistence](../../docs/reference/state.md#event-completion-persistence-h14-extension)
- [Implementation plan](../../docs/plans/history-and-recovery.md)
