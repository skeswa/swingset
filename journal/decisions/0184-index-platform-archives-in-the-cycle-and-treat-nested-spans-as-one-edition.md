# D-0184: Index platform archives in the cycle, and treat nested spans as one edition

Recorded: 2026-09-21  
Decided by: agent  
Topic: Historical backfill  
Supersedes: —  
Superseded by: —

## Decision

Each cycle that is not under backpressure ends its acquisition phase with
up to `scheduling.archive_index_seconds` of CDX index queries for every
enabled platform, one query per source, prefix and capture year from
`history_start` onward, after the pages already offered had their turn. The
shipped configuration sets 120 seconds; unset disables the step, which is
what the offline test fixtures rely on. The prefixes are the
ones each playbook's backfill section names. A failed index answer skips
that source for the cycle and never fails the cycle.

The inventory projector treats dated listings whose spans all nest inside
one widest span as one edition dated by that widest span. Spans that only
partly overlap, or fall in different months, still open the
"multiple dated editions" finding.

## Why

Every platform playbook lists the CDX query as step 1 of backfill and the
plan selects captures from `archive_captures`, but nothing in the cycle ever
ran those queries: production held index rows only for the WSDC calendar,
so all 6,247 planned platform pages had no capture and would have fallen
through to origin fetching. Of the 39 open edition findings, a review agent
found that all but three came from scoring.dance recording a single day of
a weekend that the calendar dates in full.

## Links

- [Backfill reference](../../docs/reference/backfill.md#the-wayback-transport)
- [D-0180](0180-agents-adjudicate-year-acceptance-and-identity-review.md), the review that found the span pattern
