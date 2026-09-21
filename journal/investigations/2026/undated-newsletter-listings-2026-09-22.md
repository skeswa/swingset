# Eight undated newsletter listings from 2022

Date: 2026-09-22  
Type: Review (D-0180), by a Sonnet review agent with web lookups  
Topic: Phase-one findings that block the 2022 and 2023 event lists

## Question

WSDC Newsletter Vol 22 (June 2022) and Vol 23 (September 2022) print eight
"Upcoming Registry Events" bullets with no date, and the inventory has no
edition of those events in the window each issue covers. Did the editions
take place? A "no" for a whole list makes its finding dismissible; a "yes"
means the inventory is missing an edition.

## Answer

| Newsletter | Event                                        | Verdict                 | Evidence                                                                                                                                                                           |
| ---------- | -------------------------------------------- | ----------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Vol 22     | Portland Dance Festival                      | not held                | printed in the issue's purple "cancelled due to Covid-19" colour; no 2022 edition found elsewhere                                                                                  |
| Vol 22     | Toronto Open Swing & Hustle Championships    | not held                | purple; toshc.com results archive has 2010 to 2014 and 2023 to 2025, no 2022                                                                                                       |
| Vol 22     | Sea Sun & Swing Camp                         | not held, weakly        | purple; site captures exist for summer 2022 but could not be read                                                                                                                  |
| Vol 22     | Odyssey West Coast Swing                     | not held (owner's call) | listing aggregators mirrored a Facebook event for 29 July 2022; the organiser's own page cancels 2020, posts nothing in 2021 or 2022, and calls the July 2023 edition a "relaunch" |
| Vol 22     | Riga Summer Swing                            | not held                | purple; danceconvention.net lists 2016, 2018, 2019 and 2024 only; 2023 calendar row says on hiatus                                                                                 |
| Vol 23     | Palm Springs New Year's WCS & Hustle Classic | not held                | no edition anywhere after January 2020                                                                                                                                             |
| Vol 23     | Spotlight New Year's Celebration             | not held                | purple in the issue                                                                                                                                                                |
| Vol 23     | New Year's Swing Fling                       | unknown                 | printed in white, no date; no page, capture or listing found either way                                                                                                            |

The Vol 22 finding is dismissed on the owner's evidence (2026-09-22, the
organiser's Facebook page saved from the owner's browser): the review agent's
"held" verdict rested on two listing aggregators that mirror a Facebook event
created in advance, and the organiser's March 2023 post calls that year's
edition a relaunch. Vol 23 stays open for one event with no evidence either
way; it was dismissed later the same day on the owner's direction (see
below).

## What would close them

- **Odyssey West Coast Swing 2022.** Closed by the dismissal above. The
  general gap remains: an edition that ran but earned no registry points has
  no row to alias to, and the override files cannot create one. Two options
  from the [data quality plan](../../../docs/plans/data-quality.md): a reviewed
  events override with a month and day span, or a parser rule that gives an
  undated sidebar bullet month precision from the issue's window.
- **New Year's Swing Fling 2022 to 2023.** Still unknown after two more
  passes on 2026-09-22. A Sonnet agent found the event's own domain dead, the
  archive unreachable and nothing on the aggregators. A Codex run with web
  search (32 searches, see [the Codex note](codex-exec-lookups-2026-09-22.md))
  found the WSDC newsletter of November 2023 labelling the event "On Hiatus"
  for the following new year, and no edition after January 2020 in the
  scoring.dance archive, but nothing that names the 2022 to 2023 season
  either way. The inventory has no edition for two seasons, not one. The
  weight leans to "not held". On the owner's direction to resolve the
  blockers on best judgment (2026-09-22 evening), the Vol 23 finding is
  dismissed on that weight: an official hiatus label a year later, no
  results, no registry points, no listing for the season anywhere, and a
  dead site. If a source ever names a 2022 to 2023 edition, delete the
  dismissal row and the year reopens.

## Two lessons

The newsletter's purple colour is its own "cancelled due to Covid-19" code,
and seven of the eight undated bullets are purple. It agreed with every
verified outcome here, but it stays a hint, not evidence, as the
[colour review](newsletter-colour-review-2026-09-17.md) decided. And an
event listing on an aggregator or a Facebook event page shows that an
edition was announced, not that it ran; the organiser's own posts after the
date are the evidence.

## Method

Retained PDF text and colours, the local inventory, and manual page fetches
(at most a handful per host, spaced): danceplace.com, stayhappening.com,
toshc.com, danceconvention.net, seasunswing.com captures. No project source
site was fetched outside the pipeline's rules.
