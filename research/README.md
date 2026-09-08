# research/

One-off research artifacts. Not used by the pipeline. Facts here were
captured on 2026-09-04 and are not kept up to date.

## events.csv

Every event on the WSDC calendar (`worldsdc.com/events/`) that started on
or before 2026-09-04 and ended on or after 2025-09-04. One row per event
edition.

How it was built (`build_events.py`):

- The live calendar page lists only upcoming events, so past editions
  come from Wayback Machine snapshots of the same page dated 2025-05-12,
  2025-10-06, 2025-12-19, 2026-01-07, 2026-03-08, 2026-05-13, plus the
  live page on 2026-09-04. A 2026-08-13 snapshot exists but is
  zstd-compressed and was skipped; the 2026-05-13 snapshot already covers
  its window.
- Rows are keyed by `event_key` and merged across snapshots. The latest
  snapshot wins for name, dates, website, and location.
- Coverage gap (**unverified**): an event added to the calendar after
  2025-05-12 and removed before 2025-10-06 would be missing.

Columns:

| Column | Meaning |
|---|---|
| `event_key` | `<yyyy-mm>-<series_slug>`. `yyyy-mm` is the end-date month. Same rule as `event_id` in [design/data-model.md](../design/data-model.md#identifiers). Use this to cross-reference every other research CSV. |
| `name` | Name as printed on the calendar (latest snapshot) |
| `series_slug` | Name lowercased, ASCII-folded, with years, ordinals, roman numerals, and "hiatus" removed |
| `start_date`, `end_date` | ISO dates from the calendar |
| `city`, `region`, `country` | Split from the calendar's free-text location on commas. Dirty; the calendar has typos and inconsistent country names |
| `country_code` | ISO 3166-1 alpha-3 from the flag link, sometimes blank or `transparent` |
| `event_type` | `Registry Event`, `Trial Event`, or blank |
| `flags` | Row CSS class: blank, `event-trial`, `event-unconfirmed`, `event-canceled` |
| `status` | `ended`, `in_progress` (as of 2026-09-04), `hiatus` (name says hiatus), `canceled` |
| `website` | Link from the calendar |
| `first_seen_snapshot`, `last_seen_snapshot`, `snapshots_seen` | Which snapshots listed this edition |

Known quirks: `2026-03-flow-festival-nyc` and `2026-03-new-york-flow-festival`
are the same event listed twice on the calendar under two names.
`2026-01-the-australian-classic-wcsdc` has no event type.

## aggregators/

Event indexes of the three results platforms, captured once on
2026-09-04 so that research agents could match events locally instead
of crawling the platforms.

| File | Source | Rows |
|---|---|---|
| `eepro_events.tsv` | `https://eepro.com/results/event.php` | slug, title, date |
| `scoringdance_events.tsv` | `https://scoring.dance/enUS/recent` (noscript list) | event_id, title, dates |
| `dcn_events.tsv` | `https://danceconvention.net/eventdirector/en/eventsarchive` and `eventsarchive:loadyear?year=2025` (XHR) | event_id, name, dates, location, results_published, affiliations, event_page |

## results-sources.csv

Where each event in `events.csv` published results, scores, callbacks,
and heat sheets. Built by the `wcs-results-sources` workflow: one small
agent per event checked the local aggregator indexes first, then the
event's own website (at most 6 requests per event), then a follow-up
agent retried events with nothing found. Joined to `events.csv` on
`event_key`. Workflow return values are kept in `workflow-output/`, and
`build_results_sources.py` takes them in order, later runs overriding
earlier ones per event, so the CSV can be rebuilt from the repo:

```
python3 build_results_sources.py workflow-output/*.json
```

Treat every row as a lead, not a fact. `confidence` and `evidence` say
how the agent got there. `passes` is 1 or 2 (a follow-up agent ran).
`url_in_index` is `yes` when the results URL's event id or slug exists
in `aggregators/`, `no` when the agent found the URL some other way,
`n/a` for platforms without a captured index.

Manual corrections go in `results-sources.overrides.csv`, one row per
`event_key`; any non-empty cell replaces the agent's value when the
build script runs, and the row gets `passes=manual`. Do not edit
`results-sources.csv` by hand. `source_run` names the workflow output
file a row came from; `edition_held` (yes/no/unknown) is only filled by
the retry run and says whether the edition took place at all.

Platform counts after the 2026-09-05 retry (181 events):

| `platform` | Events | Notes |
|---|---|---|
| `scoring.dance` | 87 | Prints bibs and WSDC ids. The `/enUS/recent` list holds past events only; the sitemap adds upcoming ones and City of Angels 2026 (id 315). |
| `eepro` | 38 | |
| `danceconvention.net` | 18 | Names and places on the page; bibs only in per-round PDFs. The archive listing is not exhaustive: older editions exist under 7-digit ids (WesterOz 2018 is 1601070) that the listing never shows. |
| `worlddanceregistry` | 14 | `scores.worlddanceregistry.com/<uuid>` ("Pro Score"). Not covered by the design yet. Mostly North American events (Trilogy, Swing City Chicago, Chicago Classic, Montreal Westie Fest, Carolina Summer Swing, Florida Dance Magic, Desert City Swing, and others). Pages are React Static builds with `/awards` (final results) and `/rounds` (round details) routes and a `lastBuilt` timestamp in `window.__routeInfo`. No public index; the bucket root, robots, and sitemap return 403. |
| `event_website` | 7 | HTML or PDFs on the event's own site. |
| `google_drive_or_sheets` | 2 | Mountain Magic posts one PDF per division and round in a public Drive folder. |
| `other` | 6 | UCWDC results PDFs (Texas Classic, Chicagoland), Florida Classic Series blog, Charlotte WestieFest results page, Colorado Country Classic, and Swing Fiction's own JSON API (`api.swingfiction.cz`, see overrides). |
| `not_held` | 4 | `edition_held=no`: Sea to Sky 2025, The Australian Classic 2026 (cancelled for low ticket sales), Dance N Play 2026, Toronto Open 2026. |
| `not_found` | 5 | See below. |

Still unresolved after the retry:

| `event_key` | What we know |
|---|---|
| `2025-11-cash-bash` | Site links only a World Dance Registry registration page for the 2026 edition. Results for 2025 are probably on WDR under an unknown uuid. |
| `2026-02-westeroz-swing` | Retry agent matched DCN event 1601070, which turned out to be the 2018 edition. Corrected by override. |
| `2026-05-canadian-swing-championships` | Site links only two Facebook groups. The danceplace listing's results tab is empty. |
| `2026-06-next-level-swing` | Site now advertises May 2027; whether the 2026 edition ran is unclear. |
| `2026-09-korea-westival` | Ended 2026-09-06. DCN event 301273270 exists with results not yet published. |

The retry run used Sonnet agents with WebFetch only, because the
session's WebSearch allowance was still exhausted. Web search was done
by fetching Brave's results page; running 12 agents at once got that
rate-limited (HTTP 429) for about half of them, so the retry is also
not a clean negative. One retry answer was wrong (WesterOz, above) and
one was a listing rather than results (Canadian Swing Championships);
both are corrected in `results-sources.overrides.csv`. Swing Fiction was
resolved by hand afterwards: its site is a client-rendered app, but the
bundle names a public JSON API that lists every competition run with
bibs, heats, partners, and placements.

First-run defect, kept for the record: the session's web-search
allowance (200 searches) ran out about 160 events in. 21 events, mostly
with end dates in August and September 2026, had every WebSearch
refused, and all 12 original `not_found` rows were among them. The
retry covered those 12. The other 9 (grand-party-sofia, manneken-swing,
new-england-dance-festival, rolling-swing, swing-creation-hamburg,
the-bend-connection, jax-westie-fest, south-bay-dance-fling, and
desert-city-swing before its override) were resolved from indexes or
the event site without search, so their `secondary_platforms` and
callbacks and heat-sheet fields may be thinner than elsewhere.

Things the agents could not settle without more requests: whether
callbacks and heat sheets exist for most events (`has_callbacks` is
true for 70, `has_heat_sheets` for 26), and whether bibs or WSDC ids
are visible on danceconvention.net and worlddanceregistry pages
(`bibs_visible` and `wsdc_ids_visible` are `unknown` for 54 and 82
events).
