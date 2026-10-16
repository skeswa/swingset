# D-0260: An unreported edition is one row per series and month, with its listings' sources, span and place

Recorded: 2026-10-15, amended 2026-10-16 after the blocked candidate `cand_ce7e0a45f402400a`  
Decided by: agent, 2026-10-15, under D-0172; source: session instruction of 2026-10-15 to clear the D-0257 activation blockers (maintenance item 35)  
Topic: Event identity  
Supersedes: the inventory's one-row-per-printed-name rule for editions the registry has not reported; amends D-0256  
Superseded by: —

## Decision

- All listings of one series that end in one month, with no held
  occurrence, are one edition, and a results listing joins it when its
  dates overlap or touch, within a day, a calendar-family listing's
  (`history.near`, the same test the map applies). Its id is the
  oldest, then shortest, existing unheld row that a calendar-family name
  gives or that only such listings wrote; else a results listing's row;
  else the shortest calendar-family id. The map sends the results there
  (`alias`): a row of the listing's series, else the one its name gives,
  else one whose own name resolves to that series; never another series.
  The 2024 trials (Aloha Open, Bend Connection, WCS Party, Flowstate,
  Slovenian Open, Spring Time Swing) are one event each.
- Until the calendar contract is active, a listing printing a cancel or
  hiatus note keeps its own row (`KEEP_CANCELLED_TWINS`): the release still
  shows calendar rows from its baseline, so a merge would drop the twin's
  `cancelled` (Monterey Swingfest 2021). About 150 such twin rows stay in
  state, withheld from the release while the calendar-family contracts are
  in shadow. `swingset contract activate` refuses those kinds while the
  switch is on; the activation deploy turns it off.
- Rows only listings wrote that no listing writes any more go when they are
  twins of the edition or sit beside a held occurrence of their series and
  month, unless a scope, contest, judge, registry placement, map row,
  proposal or origin intent names them.
- A row's `sources` and `history_source` are the union over every listing
  of the edition, the one the widest span leaves out too; a held row adds
  `wsdc_registry`. Its dates are the widest span that holds every
  listing's, so a results site's one day keeps the weekend (D-0184). Names
  stay as written: a held row keeps the registry's printed name.
- Status comes from the WSDC calendar's listings, else the other
  calendar-family ones; a trial mark makes it `trial`; a cancel mark, or a
  hiatus mark seen before the edition began, cancels it. Held stays held.
- City, region and country come from the newest calendar listing; none is
  cleared, and a non-alpha-3 flag gives no country.

## Why

The first deploy published empty registry headers beside their results
(the widest-span filter dropped the results source, so the map split them),
duplicate 2024 trials, and lost Monterey's `cancelled`. Re-measured
read-only: ten results listings move onto their WSDC rows, with no new
duplicate in the release and no published date, place or status lost.

## Links

[Calendar contract measurement](../investigations/2026/calendar-contract-2026-10-15.md)
