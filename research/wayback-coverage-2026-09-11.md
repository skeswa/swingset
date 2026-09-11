# What the Wayback Machine holds for a 2010 start

Checked 2026-09-11 with the queries recorded in `verification/2026-09-11/`.
Numbers come from `wayback_coverage.py` and from the archived registry dump
(blob `ae7f2b9d…`, `wsdc.mechstack.dev/data.json`, captured 2026-09-09),
read on the VM with a throwaway script. This note informed
`design/backfill.md`. It is a snapshot; it is not kept up to date.

## Results platforms in the archive

| Prefix                                             | 200-status URLs          | Events                       | Capture years | Note                                                                                                                                                 |
| -------------------------------------------------- | ------------------------ | ---------------------------- | ------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- |
| `eepro.com/results/*`                              | 1,124                    | 148 slugs                    | 2018 to 2026  | slugs by year: 2018: 8, 2019: 16, 2020: 1, 2021: 6, 2022: 17, 2023: 19, 2024: 31, 2025: 36, 2026: 13. Nothing before 2018 except two 2016 PHP pages. |
| `scoring.dance/enUS/events/*`                      | 3,155 (2026-09-08 count) | 330 ids                      | 2021 to 2026  | earliest capture 2021-06-15                                                                                                                          |
| `danceconvention.net/eventdirector/en/eventpage/*` | 1,188 (2026-09-08 count) | 415                          | 2017 to 2026  | earliest event page 2017-06-27; 6-digit ids then                                                                                                     |
| `scores.worlddanceregistry.com/*`                  | 34 (2026-09-08 count)    | 7                            | 2022 to 2026  |                                                                                                                                                      |
| `steprightsolutions.com` (HTML)                    | 1,873                    | 163 slugs, 1,685 round pages | 2013 to 2025  | round pages cover events from 2009; see below                                                                                                        |

Step Right Solutions, per event year (slugs / slugs with round pages /
round pages): 2009: 3/3/42, 2010: 4/4/58, 2011: 5/5/100, 2012: 6/6/137,
2013: 15/15/261, 2014: 17/17/340, 2015: 14/14/349, 2016: 15/13/203,
2017: 17/6/47, 2018: 23/4/41, 2019: 19/4/29, 2020: 4/1/14, 2022: 8/1/15,
2023: 9/4/27, 2024: 4/1/22. The origin returns empty 200s today
(`design/sources.md`), so the archive is the only copy.

Page shapes seen (three `id_` fetches): the events index lists every
series with city and a link per year; an event page lists contests with
Prelims, Semi-Finals, Finals links to `/events/<slug>/round/<id>`; a
prelims page has one table per role with `BIB#`, `Name`, one column per
judge, `Total`, marks `1`, `2`, `3`, judges named above the table but
columns anonymous ("Judging results are anonymous. Chief judge scores are
kept private"); a finals page has `BIB`, `Leader`, `Follower`, per-judge
placements, `Placement` (`1st`). Whether promotion is marked on prelims
rows is **unverified** (not visible in text; may be a row class).

## The WSDC calendar in the archive

`worldsdc.com/events/` months with at least one 200 capture: 2016: 4,
2017: 0, 2018: 0, 2019: 7, 2020: 5, 2021: 2, 2022: 0, 2023: 4, 2024: 5,
2025: 5, 2026: 4. There is no capture of any `worldsdc.com` URL before
2016-10-30. Whether WSDC ran its calendar on another domain before 2016
is **unverified**; no candidate domain was checked.

## The registry as the event list

The dump has 362 series, 2,653 event occurrences (series plus month),
196,679 placements. Occurrences and placements per year since 2010:

| Year          | Occurrences | Placements | Dancers with points |
| ------------- | ----------- | ---------- | ------------------- |
| 2010          | 80          | 4,957      | 1,931               |
| 2011          | 85          | 5,742      | 2,276               |
| 2012          | 91          | 6,530      | 2,626               |
| 2013          | 106         | 8,005      | 3,033               |
| 2014          | 117         | 8,810      | 3,521               |
| 2015          | 122         | 9,583      | 3,816               |
| 2016          | 135         | 10,708     | 4,282               |
| 2017          | 148         | 12,048     | 4,633               |
| 2018          | 146         | 11,277     | 4,476               |
| 2019          | 153         | 12,192     | 4,781               |
| 2020          | 31          | 2,506      | 1,823               |
| 2021          | 26          | 1,885      | 1,208               |
| 2022          | 92          | 7,843      | 3,205               |
| 2023          | 120         | 11,839     | 4,504               |
| 2024          | 139         | 14,645     | 5,508               |
| 2025          | 172         | 17,564     | 6,453               |
| 2026 (to Sep) | 123         | 12,871     | 5,530               |

1,886 occurrences and 159,005 placements since 2010-01. Occurrence dates
are first-of-month (`2014-03-01`): month precision only. 229 of 362
series carry a website URL.

## Wayback host behavior

- `robots.txt` is 404. The terms page needs JavaScript; read it by hand.
- CDX: large-prefix queries took 8 to 24 s; three timed out at 30 s.
  `showNumPages` works (EEPro: 2 pages). `id_` body fetches answered in
  under 1 s with `memento-datetime`, `x-archive-orig-*` (the origin's
  headers at capture time), `x-archive-src` (WARC file), and a `link`
  header with the timemap and neighbouring mementos. Responses also carry
  `X-RL` and `X-NA` headers whose meaning is **unverified**.
- No 429 or 503 in 27 requests at a 10 s gap.

## Origins

- `eepro.com/results/<year>/` is 404 (2012 and 2015 tried); the year
  index in the design does not exist. `eepro.com/results/liberty2018/`
  is a 200 index page, so old slugs are still served even though
  `event.php` lists only 2024 to 2026. How far back they go is
  **unverified**; ask the operator instead of probing.
