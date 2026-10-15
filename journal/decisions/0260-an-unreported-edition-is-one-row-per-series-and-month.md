# D-0260: An unreported calendar edition is one row per series and month, and carries the listing's place

Recorded: 2026-10-15  
Decided by: agent, 2026-10-15, under D-0172; source: session instruction of 2026-10-15 to clear the D-0257 activation blockers (maintenance item 35)  
Topic: Event identity  
Supersedes: the inventory's one-row-per-printed-name rule for editions the registry has not reported; amends D-0256  
Superseded by: —

## Decision

- Calendar, newsletter and council listings of one series that end in the
  same month, with no held occurrence, are one edition, whatever note a
  name gained (`Bavarian Open (Cancelled due to Covid-19)`). Its id is the
  oldest existing listing-only row of that series and month, then the
  shortest; else the shortest id a listing name gives. Results-platform
  listings keep their own row, which takes the edition's status and place.
  The one published id that goes is
  `2021-01-monterey-swingfest-cancelled-due-to-covid-19`; its status moves
  to `2021-01-monterey-swingfest`.
- The other listing-only rows of that series and month go, unless a scope,
  contest, judge, registry placement, map row, proposal or origin intent
  names them.
- Status comes from the WSDC calendar's listings of the edition, else from
  the other calendar-family ones; a newsletter read in 2026 does not
  outrank them. A trial mark on any of them makes it `trial`. A cancel mark
  cancels it; a hiatus mark cancels it only when seen before the edition
  began, or when every listing carries one. A held occurrence stays held.
- A capture that dates two spans that do not nest keeps both listings
  apart (Korea Westival 2020).
- The inventory writes city, region and country from the newest WSDC
  calendar listing of the edition. Each of the three keeps its old value
  when the newer listing has none; a flag that is not an alpha-3 code gives
  no country.

## Why

With the calendar contract active (D-0257), 153 twins would publish and
five September 2026 events would lose their place. Read-only on
production: twin groups fall from 165 to 1 (Korea Westival 2020, two
dated spans), 184 twin rows go, and no published event loses dates or
place except the stale `wsdc-342` Global Grand Prix 2026 row, whose place
now sits on the held `wsdc-409` row. Cities rise from 165 to 391. Against
the release, 121 events become `cancelled` (calendar notes such as
`(Cancelled due to Coronavirus)` the twins held), none loses `cancelled`,
and no `trial` changes.

## Links

[Calendar contract measurement](../investigations/2026/calendar-contract-2026-10-15.md)
