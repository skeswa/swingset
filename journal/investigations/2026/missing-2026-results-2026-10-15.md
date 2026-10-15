# Where the missing 2026 WSDC results are

2026-10-15. Release `28f8196` has score sheets for 113 of the 149 WSDC events
held in 2026. This page covers the other 36. Three are duplicate rows, not
missing events (two Global Grand Prix rows and `2026-10-riverswingnights`,
maintenance item 33), so 33 events were diagnosed.

## Method

One Haiku agent per event, launched by the session in one workflow. Each got
the event's facts from the release and could search the web. Agents fetched
only the event's own site, with the project User-Agent, after reading
robots.txt, at most six requests, three seconds apart. They did not fetch
shared result hosts; they reported links to them instead. The session then
checked the claims about hosts we collect against `state.sqlite`
(`source_events`, `watches`). Agent findings are **unverified** unless this
page says so.

## Findings

| Results are on                                       | Events                                                                                                                                                                                                                                                                                                                         | Verified                           |
| ---------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ---------------------------------- |
| danceconvention.net, which we do not collect         | HONEY FEST, Swingsation, Americano Dance Camp, Revitalise WCS, St. Petersburg WCS Nights, Austin Rocks; probably Swing & Snow and Southern Lights Swing                                                                                                                                                                        | no                                 |
| World Dance Registry, collected but not discovered   | Charlotte WestieFest; Jax Westie Fest (no URL given)                                                                                                                                                                                                                                                                           | Charlotte: its UUID is in no watch |
| scoring.dance, collected but not matched             | The Aloha Open (`scoringdance:375`, 2026-09-23)                                                                                                                                                                                                                                                                                | yes                                |
| The event's own site: PDFs or a script-rendered page | High Desert Dance Classic (PDFs), Swing Fiction (`score.swingfiction.cz`), South Bay Dance Fling (`sbdf.dance/results`), Canadian Swing Championships (Wix page), Calgary Dance Stampede                                                                                                                                       | no                                 |
| Not posted yet, or posted late                       | Best of the Best WCS, Boogie By The Bay                                                                                                                                                                                                                                                                                        | no                                 |
| Not found                                            | Swingvasion (high confidence none online), Autumn Beat (may not have run), Korea Westival, Meet Me In St Louis, WCS Party in Vienna, Montreal Westie Fest, Norwegian Open WCS, Shakedown Swing, Colorado Country Classic, Chicagoland, New England Dance Festival, Westie Harbor, Sea Dance Fest, Trilogy Swing, Texas Classic | no                                 |

Two agent claims are wrong. Trilogy Swing pointed at scoring.dance event 179,
which is the empty Hippmann Fun-Competition page retired on 2026-10-12. Texas
Classic pointed at `eepro.com/results/2026/`, but none of eepro's 34 events
for 2026 is Texas Classic. Both stay in "not found".

## Why the World Dance Registry is missed

WDR has no index. The guide's automatic feeder, a scan of the event's own
website ([source guide](../../../docs/reference/sources/world-dance-registry.md) §4),
was never built: production has no event-site watch, and all 13 WDR events
come from `source_urls.csv` rows. 21 of the 33 events have no calendar facts
in the release, only the registry occurrence (the 18 counted first missed
three September events).

**Traced 2026-10-15 (read-only).** The calendar does list all 21, under the
registry's own names, in the stored captures (Wayback 2025-05 to 2026-08,
origin 2026-09-21). In live state every one of them already has the
calendar's dates and website on its registry occurrence, so matching works.
Their header rows cite Wayback calendar captures fetched on 2026-09-22, and
`wsdc_calendar.events` has no admission contract, so the build publishes the
baseline's registry-only row (D-0251). Publishing them needs the calendar
contract (fix 3 of [release losses](release-losses-2026-10-12.md)). The same
read found two more defects:

- Four September occurrences (Austin Rocks, Sea Dance Fest, WCS Party in
  Vienna, The Aloha Open) stayed `listed`: a listing wrote the row before the
  registry reported the edition, and the registry never adopted it. Fixed in
  [D-0256](../../decisions/0256-past-wsdc-events-reach-their-calendar-facts-and-wdr-pages.md).
- The live calendar watch `https://worldsdc.com/events/` is `sealed`. The
  2026-09-22 phase-one intake read a Wayback capture of that URL on the same
  watch, and the parse sealed it, so the calendar has not been fetched since.
  The intake now restores the live row; the watch itself needs reopening.

Most of these events were over long before 2026-09-22, so even a built scan
(14 days before to 30 after) would not have read their sites. D-0256 adds
an offline report that reads past events' sites and drafts the WDR rows.

## What would close the gap

1. A danceconvention.net adapter: about six to eight events, the most
   unverified claims of the lot.
2. Add the reviewed WDR rows the event-site report drafts (Charlotte's
   first), reopen the live calendar watch, and review a calendar admission
   contract so calendar dates and websites publish. Built as D-0257
   ([measurement](calendar-contract-2026-10-15.md)): all 21 gain dates and
   website, none a place.
3. Match The Aloha Open's scoring.dance listing to its calendar event.
4. Event sites with PDFs or custom pages need per-event work and stay last.

## Update, 2026-10-15 (four follow-up agents)

The follow-ups checked each group against stored data or the sites themselves.

- **danceconvention.net:** its archive lists 14 published 2026 WSDC events,
  all with public results: Go West, Best of the Best, Austin Rocks, Sea Dance
  Fest, Korea Westival, Shakedown Swing, St. Petersburg WCS Nights,
  Revitalise, Americano, Swingsation, HONEY FEST, Swingvasion, Southern
  Lights and Swing & Snow. Five of them were "not found" or "late" above. The
  `dcn` source reads their finals (D-0255,
  [investigation](dcn-results-source-2026-10-15.md)).
- **Event sites:** High Desert Dance Classic, South Bay Dance Fling and
  Calgary Dance Stampede post SwingDirector PDFs; one parser reads all three
  (D-0258). Swing Fiction's feed has marks but no placements; Canadian Swing
  Championships posted nothing for 2026.
- **The Aloha Open and WCS Party in Vienna are not missing.** Their results
  sit on a second, unheld copy of the event (`2026-09-aloha-open`,
  `2026-09-wcsparty`); the registry never adopted the calendar row (D-0256,
  [investigation](aloha-open-2026-10-15.md)).
- **Calendar facts:** the 21 registry-only events do have the calendar's
  dates and website in state. The release drops them because their header
  rows cite calendar captures, and the calendar page kind has no admission
  contract.
- **WDR:** the event-site link scan the source guide describes was never
  built; all 13 WDR events came from `source_urls.csv`. Charlotte WestieFest
  2026 is confirmed on WDR. The calendar watch was sealed by the 2026-09-22
  intake, so no live calendar fetch has run since 2026-09-21 (D-0256).

## Approved rows, held for the next full deploy

The owner approved these on 2026-10-15 after the independent check. They wait
for the deploy with maintenance item 20, because an `overrides/` edit takes
effect at once and re-runs every project unit.

```
overrides/source_urls.csv:
2026-02-charlotte-westiefest,worlddanceregistry,event,https://scores.worlddanceregistry.com/20774cdb-8354-11f0-9a29-0aa72bbce9ea/rounds/routeInfo.json,wdr.rounds,"Charlotte WestieFest 2026 results page links this WDR id; created 2025-08-27, after the 2025 edition [owner approval 2026-10-15]"
overrides/series_aliases.csv:
ATX ROX 2026 - West Coast Swing Magic,wsdc-300,dcn,"Austin Rocks 2026 on danceconvention.net (atxrox.com, 2026-09-18) [owner approval 2026-10-15]",,
KOREA WESTIVAL 2026_10th Anniversary,wsdc-268,dcn,"Korea Westival 2026 on danceconvention.net, held 2026-09 [owner approval 2026-10-15]",,
```

Check each row's column order against the file header before adding it.
