# Event-list source checks, 2026-09-12

Fetched with `polite_fetch.py` (10 s gap to the archive, 5 s elsewhere,
one request in flight). Headers and `.meta` lines are kept for every
request; HTML and PDF bodies were deleted after reading. CDX responses are
kept as `.json`. Report: `research/event-list-sources-2026-09-12.md`.

| Folder         | What was fetched                                                                                                                                                                    |
| -------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `.`            | CDX, `swingdancecouncil.com` domain, 2008 to 2016, collapsed to one row per day (`r00_web_archive_org.json`). An earlier distinct-URL query into the same names was overwritten by it |
| `asp/`         | CDX of `ActiveServerPages/*Events*` (all years, `r00.json`); the 2012-08-25 `UpcomingEvents.asp` capture; the 2013-01 newsletter page                                              |
| `asp2/`        | `UpcomingEvents.asp` captures 2009-02-05, 2015-11-28, 2016-02-03                                                                                                                    |
| `asp3/`        | `UpcomingEvents.asp` captures 2010-03-24, 2011-07-16, 2012-12-28, 2013-09-21, 2014-11-12; CDX of `worldsdc.com` event and calendar paths 2016 to 2023 (`r05.json`)                 |
| `holes/`       | `worldsdc.com/print-event-list/` 2017-06-06, `/event-list/` 2022-05-16, `/events-map/` 2017-01-24 (after two archive redirects), `NonRegUpcomingEvents.asp` 2012-03-02             |
| `holes2/`      | `worldsdc.com/events/` 2016-11-13, `/event-calendar/` 2017-06-06                                                                                                                    |
| `wsdc/`        | live `worldsdc.com/events/`, `/print-event-list/`, `/event-calendar-2/`                                                                                                             |
| `wsdc-api/`    | live `worldsdc.com/wp-json/` and `/wp-json/wp/v2/types`                                                                                                                             |
| `points/`      | live `points.worldsdc.com/` (redirects to `/lookup2020`)                                                                                                                            |
| `newsletters/` | live newsletter PDFs Vol 6 (2018-04), Vol 3 (2017-07), Vol 30 (2024-05); Vol 8 (2018-10) was read through a separate fetch and is not recorded here                                |

Local-only analysis (no requests): registry month versus calendar end
month, computed from the published `registry_placements` and `events`
Parquet files in the Hugging Face cache for revision `845d92a0`.
