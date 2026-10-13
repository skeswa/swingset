# D-0253: A results listing's contests follow its series occurrence

Recorded: 2026-10-13  
Decided by: owner, 2026-10-13 ("build the code fix, applied to all years"); agent wrote the rule under D-0172  
Topic: Event identity  
Supersedes: the map's exact-name-only rule (D-0222 kept it) and the inventory's fixed `registry` status for results-platform listings  
Superseded by: —

## Decision

- After an override, `project/map.propose` proposes the held registry
  occurrence that the inventory's series-and-month rule names, with
  `match_method = alias`. The series is the reviewed `series_aliases.csv` row
  for the listing's printed name and end month, else the one registry series
  printed with that name. The event is the one held occurrence of that series
  in the end month or the next. The inventory and the map both call
  `project/history.match_listing`.
- The map takes that event only when the inventory already gave it the
  listing's source and its dates overlap the listing's. Otherwise the old
  exact-name rule, then a placeholder, apply as before.
- A held registry occurrence labelled `unknown` becomes `registry`; `trial`
  stays. This covers a placeholder or calendar row the registry adopts.
- A results-platform listing gives its event the status of the newest
  calendar listing of the same edition, else `registry` for a `wsdc-*`
  series and `unknown` for any other.
- The rule applies to every year from `history_start` (2010).

## Why

The inventory put a listing's header facts on the WSDC event while the map
put its contests on a placeholder (`2026-06-liberty-swing` beside
`2026-06-liberty-swing-dance-championships`), and adopted placeholders kept
`unknown`. In release `3fe7bae` only 12 of 62 past 2026 WSDC events showed
results. The rule reuses series decisions the inventory already publishes;
the alias rows are D-0180 reviews.

## Alternatives and consequences

One `event_aliases.csv` row per edition (81 today, more every season) fixes
the splits but not the labels. Dropping words in `normalize_event_name`
guesses ("German Open" is also a bachata event). Costs: placeholder events
leave the release, so the `events` gate measure falls and needs an approval
row; moved contests, rounds and entries change their event prefix; years
whose event list changes need `accept-year` again. A wrong alias row now
moves contests as well as header facts.

## Links

[Event-matching investigation](../investigations/2026/event-matching-2026-10-13.md)
