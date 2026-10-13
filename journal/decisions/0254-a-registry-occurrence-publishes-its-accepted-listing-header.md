# D-0254: A registry occurrence without an admissible header publishes its accepted listing's header

Recorded: 2026-10-14  
Decided by: agent, 2026-10-14, under D-0172; source: owner instruction of 2026-10-14 to clear the D-0253 release blockers  
Topic: Publication  
Supersedes: in part, D-0251 (the stand-in's status, and the gate's per-event rule) and D-0224 (an event with no admissible header row is withheld)  
Superseded by: —

## Decision

- When every `events` row of an event is a rejected legacy row and the
  baseline lacks the event, or the baseline's own header cites a revoked
  snapshot, the partitioned build publishes a header made
  from an accepted listing mapped to it, if the event is a held occurrence of
  a `wsdc-*` series. Its event id, series, month, `held` and status are the
  registry occurrence's; its name, dates, source, snapshot and parser are the
  listing's; place, website and live window stay empty. The listing must
  cite an `accepted` snapshot and parser; the widest span, then the newest
  snapshot, wins (`build/partitions._listing_header`).
- A legacy result row of an event that took a placeholder's listing is kept
  when the same row under the placeholder's ids (the id the map generates for
  that listing) equals the baseline's row: it was published already. Only
  the partitioned build does this and the listing header.
- D-0251's baseline stand-in keeps the baseline's columns but takes the
  rejected row's status in two cases: `unknown` to `registry` for a held
  `wsdc-*` occurrence of the same series, and `registry` to `unknown` for a
  series that is not a registry series. Both build paths do this.
- The gate's per-event check (D-0251, maintenance item 28) counts an event
  whose results moved as moved, not lost: each of its contests with results
  is in the candidate under another event, still with results, matched by
  contest id without the event prefix and any shared contest or round
  source ref. Only a lost event refuses, unless a `result_event:<id>` row
  approves it.

## Why

D-0253 moves placeholder results onto their WSDC events. 47 of those events
had headers from unassessed pages (WSDC newsletter, calendar, scoring.dance
parser 3), 46 not in the baseline, and two (SOswing 2024, Midnight Madness 2025) hold parked round pages the baseline published, so their results
would leave the release. These rules publish only facts an accepted page
gives or the baseline already published. A gate keyed on event id would
call every merge a loss, and a re-projection can take a contest's own ref
from another of its pages.

## Alternatives and consequences

A newsletter or calendar admission contract would publish the full dates,
but an edit to `admission/contracts.py` re-runs 41 of 45 recipe scopes (item
20). Choosing a platform header in the inventory would drop calendar dates
and places for many events (item 29). Cost: these events show the results
site's one-day dates and name until a calendar contract lands.

## Links

[Event-matching investigation](../investigations/2026/event-matching-2026-10-13.md)
