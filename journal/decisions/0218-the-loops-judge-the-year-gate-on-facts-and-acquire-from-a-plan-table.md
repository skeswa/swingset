# D-0218: The loops judge the year gate on facts, and acquire from a plan table

Recorded: 2026-09-27  
Decided by: agent, 2026-09-27, under D-0175; source: phase 2 of the [architecture plan](../../docs/plans/architecture.md) (finding F13)  
Topic: Acquisition  
Supersedes: —  
Superseded by: —

## Decision

In the acquire, plan and derive loops the phase-two year gate reads facts
only: acceptance, the accepted digest against the committed event list,
open event-list findings, and the H7 and H10 switches. It does not ask
whether a pending projection could move the event list
(`facts_only_year_gate()` in `project/history.py`). The cycle and
`accept-year` keep that question.

The plan loop evaluates the gate and the retained history plan every 30
minutes and writes `acquisition_plan`: one row per year and per planned
page, each `eligible` or with its stop reason. Year rows record the pending
answer as information. A pass cut by its deadline keeps the older page
rows it did not reach. The acquire loop offers the eligible page rows in
plan order; each dispatch re-checks its target from facts before any
mutation, so a row whose year's digest moved after planning is refused.

## Why

The pending question walks every pending derivation unit and was the one
derivation predicate left in acquisition (F13). It closed phase two for
every year while a map or inventory waited, and it cost seconds per pass
(D-0181, D-0195). Acquisition must not wait on derivation (I5).

## Alternatives and consequences

Keeping the question would tie acquire to derive's backlog again. The cost:
acquire may fetch sheets for an accepted year whose event list a pending
projection is about to change. The fetch is spent, not wrong: the digest
check closes the year as soon as the list is committed, and acceptance is
still the owner's (open decision 1 keeps it an acquisition input).

## Links

[Historical backfill, scheduling](../../docs/reference/backfill.md#scheduling)
