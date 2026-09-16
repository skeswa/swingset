# Wayback Machine coverage checks, 2026-09-11

Run with `polite_fetch.py` (10 s gap to `web.archive.org`, 5 s elsewhere,
`swingset/0.0` User-Agent, no automatic redirects). CDX bodies are kept
because they are small indexes, not page content; page bodies were
discarded. `../../wayback_coverage.py` reads the JSON files here.

| File | Request | Result |
|---|---|---|
| `wayback_robots.hdr` | `GET web.archive.org/robots.txt` | 404 (unrestricted under our robots rule) |
| `cdx_worldsdc_events_2008_2016.json` | `worldsdc.com/events*`, 2008 to 2016, 200 only, one per month | captures only from 2016 |
| `cdx_worldsdc_all_2008_2015.json`, `cdx_worldsdc_domain_to_2015.json` | any `worldsdc.com` URL before 2016, with and without status filter | empty |
| `availability_worldsdc_2012.json` | availability API, `worldsdc.com` at 2012-06 | closest capture is 2016-10-30 |
| `cdx_worldsdc_events_months_2016_on.json` | `worldsdc.com/events/`, 2016 on, one per month | months per year in the research note |
| `cdx_eepro_results_to_2016.json` | `eepro.com/results/*` to 2016 | two PHP pages from 2016, nothing else |
| `cdx_eepro_results_all.json` (+`.hdr`) | `eepro.com/results/*`, all years, `collapse=urlkey` | 1,124 URLs, 148 slugs, captures 2018 to 2026 |
| `cdx_eepro_results_numpages.txt` | same prefix with `showNumPages=true` | 2 pages at the default page size |
| `cdx_dcn_eventdirector_to_2017.json` (+`.hdr`) | `danceconvention.net/eventdirector/*` to 2017 | 26 URLs, all assets, all 2017; the query took 23.8 s |
| `cdx_dcn_eventpage_2013_2018_first50.json` | `eventpage/*`, 2013 to 2018, first rows | earliest event page capture 2017-06-27 |
| `cdx_scoringdance_events_2015_2021_first5.json` | `scoring.dance/enUS/events/*`, 2015 to 2021, first rows | earliest capture 2021-06-15 (event 10) |
| `cdx_steprightsolutions_html_all.json` (+`.hdr`) | `steprightsolutions.com` domain, HTML, 200, `collapse=urlkey` | 1,873 URLs, 163 event slugs, 1,685 round pages |
| `wayback_id_steprt_round_507.hdr`, `..._508.hdr`, `..._events_index.hdr` | `id_` body fetches of a prelims page, a finals page, and the events index | 200 in under 1 s; Memento and `x-archive-orig-*` headers shown |
| `archive_org_terms.hdr` | `archive.org/about/terms.php` | 302 to `/about/terms`, a page that needs JavaScript; not readable here |
| `eepro_results_2015_dir.hdr`, `eepro_results_2012_dir.hdr` | `GET eepro.com/results/<year>/` | 404: year indexes do not exist |
| `eepro_results_liberty2018_dir.hdr` | `GET eepro.com/results/liberty2018/` | 200, a 2.2 KB index page: old slugs are still served by the origin |

Failures worth remembering: three CDX queries over large prefixes
(`danceconvention.net/eventdirector/en/eventpage/*` across all years and
again limited to 2013 to 2019, and `scoring.dance/enUS/events/*` with
`collapse=timestamp:4`) exceeded the helper's 30 s read timeout. Successful
large queries took 8 to 24 s. Narrow the window with `from`/`to`, page with
`page=`, and allow CDX a longer timeout than page fetches.
