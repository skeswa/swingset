# D-0256: A registry occurrence adopts a listed edition; past WDR pages come from an offline event-site report

Recorded: 2026-10-15  
Decided by: agent, 2026-10-15, under D-0172; source: owner instruction of 2026-10-15 to make the missing 2026 WSDC results reachable  
Topic: Event identity  
Supersedes: in part, D-0253 (which row a registry occurrence adopts) and the planned event-site link scan (WDR guide section 4, scheduling step 6), which was never built  
Superseded by: amended by D-0260 (unreported editions)

## Decision

- When the registry reports an occurrence and no row of its series and
  month is held, the inventory adopts the one matching calendar edition
  (as before), else one `listed` row of that series and month: a row the
  WSDC calendar listed, else the first by id. A listing printed before the
  registry reported the edition, of a series it already knew, wrote that row.
  A `cancelled` row is never adopted. Registry placements join the held row of their series and month,
  else the first by id. No row is deleted.
- The phase-one intake puts a live watch's row back after reading a Wayback
  capture of the same URL on it; the capture's parse no longer leaves the
  live calendar sealed.
- WDR pages of past events come from `journal/tools/collection/event_site_links.py`:
  for held WSDC events without results it reads the event's site politely
  and drafts a `source_urls.csv` row for the one WDR id made in the 400
  days before the edition ended. A reviewer adds the row. No loop fetches
  event sites.

## Why

In live state all 21 registry-only events of release `28f8196` already
carry their calendar dates and website; the release shows the baseline's
registry-only row because calendar headers have no admission contract.
Four September 2026 occurrences (Austin Rocks, Sea Dance Fest, WCS Party in
Vienna, The Aloha Open) stayed `listed` beside their registry points, so no
listing could join them. The planned link scan does not exist, and it would
not reach past events anyway: their calendar facts arrived on 2026-09-22
from Wayback, months after most ended. A results page lists every year's
WDR link (Charlotte WestieFest shows four), so the id's date picks the edition.

## Alternatives and consequences

Agent 3 proposed adopting the calendar row and deleting the listing's own
row; deleting an event with contests cascades them before the map moves
them, so the orphaned `listed` rows stay (WCS Party, Aloha), empty after the
move, like `2026-09-global-grand-prix-wcs` (maintenance item 33). A live
`site` watch per event is the fuller fix but adds a source and hundreds of
hosts. The rule change re-runs the nine project scopes, link and build.

## Links

[Missing 2026 results](../investigations/2026/missing-2026-results-2026-10-15.md)
