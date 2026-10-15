# D-0257: Admit WSDC calendar captures under contract 1; activate after the history twins are fixed

Recorded: 2026-10-15  
Decided by: agent, 2026-10-15, under D-0172; source: session instruction of 2026-10-15 to build a contract for `wsdc_calendar.events`  
Topic: Sources  
Supersedes: the `unassessed` status of `wsdc_calendar.events` (D-0251, D-0254 name the gap)  
Superseded by: —

## Decision

- `wsdc_calendar.events` gets contract 1 (`admission/wsdc_calendar_contract.py`).
  It counts listings from the raw body in each of the four layouts (table,
  cards, FullCalendar, map) and needs that count to equal the dated rows
  plus `impossible_listing_date` findings. Unreviewed row classes, listing
  types and unknown date formats block. Map pins `1` and `white` carry no
  type. An unknown flag is a noncritical finding: the row keeps no country.
  An empty listing is `waiting_for_inputs`. There is no other calendar kind;
  the 2016 undated maps stay extract failures (D-0071).
- Removal authority is `watch`: a live capture replaces the previous live
  capture's rows (D-0191). An archived capture is its own unit and replaces
  only itself. The writer no longer lets a live capture delete an archived
  capture read on the same watch, and the build matches an archived
  capture's rows under the `wsdc-history` scope they are stored in.
- The adapter keeps no country for a blank or `transparent` flag (Russia
  since 2025), maps six printed country names to codes, dates a
  `Dec - Jan, YYYY` span across New Year, and withholds spans the source
  printed backwards. Parser 7 stays: the inventory does not notice a new
  parser label (item 32), so a bump would leave its rows unsupported.
- The code ships in the shadow policy with the maintenance item 20 deploy.
  The policy is activated, after an independent check, only once the
  history projection stops writing twins for annotated listings.

## Why

21 held 2026 events and 10 WDR result events publish registry-only or
month-wide rows because their headers cite calendar captures. On production
111 of 144 captures pass. Activation would date 42 published events and add
341, 29 of them held registry occurrences. It would also publish 153 twins
(`2020-09-bavarian-open` beside its `-cancelled-due-to-covid-19` row) and
drop the place of five September events, which the history projection
never carried.

## Links

[Measurement on production](../investigations/2026/calendar-contract-2026-10-15.md)
