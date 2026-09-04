# Sources

This document records what exists, what each source gives, and how we read
it. Details such as HTML selectors go in per-source playbooks under
`docs/sources/` once written. URL patterns are [below](#url-patterns).

## Summary table

| Source | Gives | Bibs | Names | WSDC ids | Judges' marks | Heats | Format | Change signals |
|---|---|---|---|---|---|---|---|---|
| WSDC registry | dancers, points, final placements | no | yes | yes | no | no | JSON via POST | none (no ETag) |
| WSDC event calendar | event list, dates, city, website | - | - | - | - | - | WordPress HTML | likely ETag (unverified) |
| EEPro | prelim marks, callbacks, final marks, placements | yes | yes | no | yes, named | partial (unverified) | static HTML, some PDF | `ETag` + `Last-Modified` |
| scoring.dance | prelim marks, callbacks, final marks, placements | yes | yes | **yes** | yes, named | unverified | server-rendered HTML | `Last-Modified`, `max-age=300`, sitemap |
| danceconvention.net (DCN) | rankings per round; per-round PDF with bibs and marks | PDF only | yes, with city | no | PDF only | app only (unverified) | Nuxt payload in HTML + PDF | `ETag` on HTML, none on PDF |
| Event-site PDFs | historical results | varies | yes | no | varies | no | PDF | none |

Heat lists are the thinnest data. All three platforms push heats through
mobile apps or wall postings. We record heats when a public page shows
them and otherwise leave the `heats` table sparse. Participation is still
complete because every competitor who danced appears on the prelims sheet.

## WSDC registry (`points.worldsdc.com`)

Verified facts:

- Laravel app. `robots.txt` allows everything. No terms of use page. No
  API key. No rate-limit headers seen.
- `POST /lookup2020/find` with form field `num=<wsdc_id>` returns JSON for
  one dancer. No CSRF token or cookie needed. The legacy `POST /lookup/find`
  still works and returns a smaller shape.
- `POST /lookup/find` with `q=<name>` returns `{"type":"names","names":[{id,first_name,last_name,wscid}]}`.
- `GET /lookup2020/autocomplete?q=<prefix>` returns name matches
  (**unverified** by us; used by third-party front ends).
- Ids are dense integers from 1 to roughly 29,000 as of 2026-09.
- The JSON is dancer-centric. For each role, then each dance style
  ("West Coast Swing", "Lindy"), then each division code, it lists
  `competitions[]` with `{role, points, result, event:{id, name, location, url, date:"Month YYYY"}}`.
  `result` is `"1"`..`"5"` or `"F"`. Full shape [below](#registry-json-shape-lookup2020find-trimmed).
- The registry stores only finalists who earned points. No prelims, no
  field sizes, no bibs. Tier must be inferred from points awarded.
- Quirks: `placements` is a dict when non-empty and an empty list `[]`
  when empty. `dancer.id` is an internal key and is not the WSDC number
  (`wscid`). Names are transliterated English only.
- There is no bulk export, no event endpoint, no "recent results" feed.
  Every view of the registry other than one dancer must be rebuilt by
  reading every dancer.
- Results reach the registry 1 to 7 days after an event. Event directors
  must submit within 3 days.
- Duplicate numbers happen and are merged by hand by WSDC staff. Name
  changes are also by hand.

How we use it:

- **Bootstrap:** one full sweep of ids 1..N at 1 request every 2 seconds,
  about 16 hours, run once. Store every response as a snapshot. Before
  the sweep, download the third-party dump below once and, after the
  sweep, diff the two: every id in the dump must be in our sweep, and
  names and placement counts must agree. Disagreements go to the review
  queue. The dump is never written into the dataset.
- **New dancers:** weekly, probe ids above the highest known id until 20
  consecutive misses.
- **Post-event confirmation:** after an event ends, refresh each finalist
  we identified, once per day, until the event appears in their record or
  30 days pass.
- **Trickle refresh:** refresh dancers not refreshed in 365 days, at most
  100 per day. This catches merges and name changes without full sweeps.
- We never call autocomplete or name search in the pipeline. Name lookup
  uses our local mirror.

Prior art: `smwa/wsdc_points_server` publishes a full dump at
`wsdc.mechstack.dev/data.json` (~39 MB). We download it once as a
cross-check for our bootstrap sweep. It is not a source of truth. Several
GitHub scrapers sweep at 3 requests per second without being blocked; we
go slower on purpose.

## WSDC event calendar (`worldsdc.com/events/`)

- WordPress table, calendar, map, and print list. Fields: start and end
  date, event name (linked to event site), city/region, country, type
  (Registry Event, Trial Event, unconfirmed). Map view adds lat/lng.
- No event id, no director, no results links, no official JSON. A WP REST
  endpoint may exist (**unverified**).
- Registry-side `event.id` is per series, matched to calendar rows by
  name only.

How we use it: fetch the print list daily. It seeds the `events` table
and defines each event's polling window.

## EEPro (`eepro.com/results/`)

- Apache static HTML 4.0. `robots.txt` returns 404, which under RFC 9309
  means crawling is allowed. No terms page seen.
- Index: `results/event.php` lists events newest first. Per event:
  `event.php?event=<slug>`. Rounds: `results/<slug>/<contest><round>.html`
  such as `asc2024/jjprelims.html`, `asc2024/jjfinals.html`. Some PDFs
  sit alongside.
- One `<table border="1">` per contest. Header row reads
  `Division: <name> <Prelims|Finals>`.
- Prelims columns: Count, Competitor (full name), one column per named
  judge (Y, A1, A2, A3, N), BIB, Counts (Y-A-N), Sum, Promote (X), Alt.
  Tie-break legend printed in the header.
- Finals columns: Place, Competitor ("Leader and Follower"), one column
  per judge (rank), BIB (`255/720` = leader/follower for J&J, single bib
  for couples), Marks Sorted (e.g. `1-1-1-1-2-2-4`).
- Serves `Last-Modified` and `ETag`. Conditional GET works.
- Companion "SwingDancer" app pushes callbacks; we do not touch it.

Heavy US coverage: Summer Hummer, Swingtacular, Arizona Dance Classic,
Midwest Westie Fest, Big Apple, Wild Wild Westie, Phoenix 4th, Liberty
Swing, JJ O'Rama, Michigan Classic, GNDC, and more.

## scoring.dance

- Server-rendered Bootstrap HTML behind Cloudflare. `robots.txt` allows
  all. `Cache-Control: public, max-age=300` and `Last-Modified` present.
  `sitemap.xml` exists. Dominant in Europe, growing in the US (Rose City
  Swing moved here).
- Event: `/enUS/events/<eventId>/results/`. Round:
  `/enUS/events/<eventId>/results/<roundId>.html`. Recent list:
  `/enUS/recent`. Dancer profile: `/enUS/wsdc/registry/<wsdcId>.html`.
- Table `table.table.w-100`. Judges in `<th title="Full Name">KS</th>`,
  chief judge marked with an icon. Rows carry `data-state="CB"` when
  called back. Name cells link to `/enUS/wsdc/registry/<id>.html` with
  `data-wsdc="<id>"`. **This is the only source that prints WSDC ids.**
- Prelims: bib, name, per-judge Yes/Alt1/No, sum. Finals: bib, leader,
  follower, per-judge rank, placement.
- Cloudflare served a JS challenge on `/enUS/wsdc/results/...` paths but
  not on `/enUS/events/...`. We only use `/enUS/events/...`,
  `/enUS/recent`, and the sitemap. If a challenge appears, the watch
  pauses for 24 hours and logs it. We do not try to solve challenges.
- A paid REST API exists (RapidAPI, and `pointstest2.scoring.dance`
  "WSDC Registry API"). Endpoints **unverified**. If it becomes available
  at a fair price, it replaces scraping for this source. See [open questions](open-questions.md).

## danceconvention.net (DCN)

- Nuxt 2 SSR + Vuetify front end over legacy Apache Tapestry pages.
  CloudFront. `Cache-Control: no-store` but an `ETag` is present on HTML.
  HTML pages are about 2.8 MB because translations are inlined, so
  conditional GET matters a lot here.
- Event lists: `/eventdirector/en/upcoming` and `/en/eventsarchive`.
  The SSR payload `window.__NUXT__` contains
  `state.common.currentPageRenderData.upcomingEvents|lastYearEvents[]`
  with `{eventId, name, startDate, endDate, location, affiliations:["WSDC"], results, published, eventPage}`.
  Other years: `eventsarchive:loadyear/<year>` (Tapestry HTML).
- Event tabs: `/eventdirector/en/eventpage/<eventId>[-slug]/{info,signups,divisions,partners,schedule,updates,results}`.
  `publishFlags` decide which are public. In samples only
  `publishCompResults` was true.
- Results tab payload: `currentPageRenderData.results[]` =
  `{contestId, contestName, divisionType: RANDOM_PARTNER|OPEN_COUPLE|PERM_COUPLE, rounds[]: {roundId, roundName, isFinal, scoresAvailable, scoresLink, rankings[]: {competitorRole, competitorName, competitorCityAndState, competitorCountry, partnerName, partnerCityAndState, partnerCountry, rank, interval}}}`.
  `roundName` is free text with typos seen ("Prelilms"). **No bib and no
  WSDC id in the payload.**
- The payload is a JavaScript function call, not JSON. We evaluate it in
  a sandboxed `node` subprocess ([parsing](parsing.md#parsing-rules)) and fall back to nothing.
  We do not regex it.
- Per-round PDFs: `/eventdirector/en/roundscores/<roundId>.pdf`. Finals:
  `# | Name (leader, newline, follower) | per-judge ranks | tally | Result`.
  Prelims: `# | Name | per-judge 10/4.5/4.3/4.2/0 | Sum | Result`. Judges
  listed above the table. Older PDFs use the 1/2.x/3 legend and may be
  localized. **Bibs appear only in these PDFs.** No validators on PDFs, so
  we fetch a PDF only when its parent HTML payload changed.
- `robots.txt` disallows registration flows and a few Tapestry actions.
  Public results and PDF paths are allowed.
- Heats page `/eventdirector/en/hybrid/event/heats/<eventId>` is an app
  WebView whose content loads client-side. **Unverified** whether public.
  Out of scope until verified.
- A REST namespace `/eventdirector/rest/v2/...` and a WebSocket exist for
  registration. Not public. Not used.

## Other platforms

- Step Right Solutions: server returns empty 200s. Dead. Historical only.
- World Dance Registry "Pro Score": client-side JS app; data endpoints
  unverified; rare in WCS. Deferred.
- Danceplace, Swing Director, SwingWars, Vote4Dance, EventManagement: on
  the WSDC approved list but no public results URLs found. Deferred.
- Event-site PDFs (e.g. Liberty Swing 2004 to 2022): one-off backfill
  parsers, lowest priority.

## Timing facts that drive the schedule

- Registration usually closes about an hour before a contest; heats are
  drawn at check-in close.
- Callbacks are announced at the start of the next round and posted on a
  wall or in an app. Scoring platforms may post per-round sheets as soon
  as they are scored (timing **unverified**).
- Finals results are announced at awards, usually Sunday, and posted
  online shortly after.
- WSDC rules require all rounds and score reports be posted and stay up
  for at least 30 days, and require corrected results be posted when
  errors are found. So a results page can change for 30 days after the
  event. No platform publishes a changelog.
- Registry postings land 1 to 7 days after the event.

## URL patterns

```
WSDC registry
  POST https://points.worldsdc.com/lookup2020/find        num=<wsdc_id>
  POST https://points.worldsdc.com/lookup/find            num=<wsdc_id> | q=<name>
  GET  https://points.worldsdc.com/lookup2020/autocomplete?q=<prefix>   (unverified, unused)

WSDC calendar
  GET  https://worldsdc.com/events/
  GET  https://worldsdc.com/print-event-list/
  GET  https://worldsdc.com/events/map/

EEPro
  GET  https://eepro.com/results/event.php
  GET  https://eepro.com/results/event.php?event=<slug>
  GET  https://eepro.com/results/<slug>/<contest><round>.html
  GET  https://eepro.com/results/<year>/

scoring.dance
  GET  https://scoring.dance/enUS/recent
  GET  https://scoring.dance/sitemap.xml
  GET  https://scoring.dance/enUS/events/<eventId>/results/
  GET  https://scoring.dance/enUS/events/<eventId>/results/<roundId>.html

danceconvention.net
  GET  https://danceconvention.net/eventdirector/en/upcoming
  GET  https://danceconvention.net/eventdirector/en/eventsarchive
  GET  https://danceconvention.net/eventdirector/en/eventsarchive:loadyear/<year>
  GET  https://danceconvention.net/eventdirector/en/eventpage/<eventId>/results
  GET  https://danceconvention.net/eventdirector/en/roundscores/<roundId>.pdf
```

## Registry JSON shape (`/lookup2020/find`, trimmed)

```json
{
  "leader": {
    "type": "dancer",
    "dancer": {"id": 96, "first_name": "Bill", "last_name": "Borgida", "wscid": 100},
    "level": {"required": "ALS", "allowed": "CHMP", "reason": ""},
    "placements": {
      "West Coast Swing": {
        "CHMP": {
          "division": {"id": 7, "name": "Champions", "abbreviation": "CHMP"},
          "total_points": 2,
          "competitions": [
            {"role": "leader", "points": 1, "result": "F",
             "event": {"id": 53, "name": "Summer Hummer",
                       "location": "Boston, MA, United States",
                       "url": "https://summerhummerboston.com/",
                       "date": "August 2002"}}
          ],
          "adv_sliding": [], "as_sliding": []
        }
      }
    },
    "recent_year": "2002"
  },
  "follower": {"type": "dancer", "dancer": {"...": "..."}, "placements": [], "recent_year": 0},
  "dancer_first": "Bill", "dancer_last": "Borgida", "dancer_wsdcid": 100,
  "dominate_role": "Primary Role Leader",
  "dominate_required": "ALS", "dominate_allowed": "CHMP",
  "is_pro": 0, "recent_year": "2002"
}
```

Division codes seen: `NEW`, `NOV`, `INT`, `ADV`, `ALS`, `CHMP`, plus
`INV`, `PRO`, `TCH`, `JR`, `SOPH`, `MSTR` (age and non-skill codes;
exact set to be confirmed from the sweep).
