# WSDC event calendar (`worldsdc.com/events/`)

The calendar provides event dates, places, and links. It does not provide complete score sheets.

[All sources](README.md) · [Shared fetching rules](../fetching.md)

## 1. Status

Verified 2026-09-08 (`journal/evidence/collection/source-survey-2026-09-08/wsdc_*.hdr`).
WSDC is the governing body; no personal relationship. `robots.txt`
allows everything and names a Yoast sitemap index. No terms page
checked for the calendar.

## 2. What it gives

Every upcoming registry and trial event: start and end date, name, link
to the event site, city, region, country, type, and row flags
(`event-trial`, `event-unconfirmed`, `event-canceled`). It does not show
past events. There is no event id.

## 3. URL patterns

```
GET https://worldsdc.com/events/           calendar (250 KB raw, 33 KB gzip)
GET https://worldsdc.com/robots.txt
```

`/print-event-list/` is 404 and `?year=` breaks the shortcode
(2026-09-04). The map view exists but is not needed.

Two Wayback captures (2026-05-21 and 2026-08-13) were stored still
zstd-encoded, as Wayback replayed them, before the client decoded zstd.
Stored bodies are never rewritten, so the extractor decodes a body that
starts with the zstd magic bytes (fixture `calendar-20260813-zstd.body`).

## 4. Discovery

The calendar is the root of discovery: each row becomes or updates an
`events` row keyed by `<yyyy-mm>-<series_slug>`. The event site link is
what the offline [event-site link report](../../../journal/tools/collection/event_site_links.py)
reads to find World Dance Registry pages; no loop scans event sites
([D-0256](../../../journal/decisions/0256-past-wsdc-events-reach-their-calendar-facts-and-wdr-pages.md)).

The live watch is `https://worldsdc.com/events/`. A Wayback capture of the
same URL is read on that watch, and parsing it seals the watch; the
phase-one intake now puts the live watch's row back afterwards. The
2026-09-22 intake ran before that fix, so the calendar was not fetched from
2026-09-22 until the owner reopens the watch (D-0256). While the watch keeps
that capture's `archive_url`, no live capture can be selected or admitted.
A live capture replaces only the previous live capture's rows; the archived
capture on the same watch keeps its own (D-0257).

## 5. Change detection (verified)

No validators; `cf-cache-status: DYNAMIC`. A GTranslate widget id
changes every response, so the raw hash always differs. Fingerprint:
the hash of the `extract` output (`docs/reference/parsing.md`), which includes the
row CSS class (cancellation and trial flags), the event site URL, and
the flag link's country code, since those live in attributes and change
without the text changing. The GTranslate ids are outside the table
and never reach the parser. Body archived only when the fingerprint
changes.

## 6. Politeness settings

```toml
[hosts."worldsdc.com"]
min_gap_seconds = 2
daily_request_budget = 10
index_interval = "24h"
```

## 7. Fetch procedure

One plain GET a day, fingerprint, parse on change.

## 8. Parsing

`wsdc_calendar.events`: rows `<tr class="..."><td>date</td><td><div class="event_name"><a href=...>name</a></div><div class="event_type">...`;
three date formats; the flag link gives the ISO 3166-1 alpha-3 code. A
blank flag, or `transparent` (every event in Russia from 2025-10 on), keeps
no country; six flags print a country name, which maps to its code
(D-0257). The map prints `Dec 28 - Jan 1, 2020` with one year: the start
takes the year before. A span printed backwards is an
`impossible_listing_date` finding and the row is withheld.

Admission: contract 1 ([parsing](../parsing.md#source-admission-h6-and-h7)),
in the `shadow` policy until the history projection stops writing twins
for listings whose names gain a note such as `(Cancelled due to Covid-19)`
([measurement](../../../journal/investigations/2026/calendar-contract-2026-10-15.md)).

## 9. Quirks

- The same event can be listed twice under two names (Flow Festival
  NYC 2026).
- Location is free text with typos; the country code from the flag is
  more reliable than the country name.
- Past editions vanish, so history comes from the Wayback Machine
  (snapshots roughly every two months in 2025 and 2026, none at all in
  2017, 2018, and 2022) and from the registry.

## 10. Backfill

Wayback captures of `/events/`, as in `journal/tools/collection/build_events.py`, read
oldest first through the Wayback transport as index snapshots. Months
with a 200 capture (2026-09-11): 2016: 4, 2017: 0, 2018: 0, 2019: 7,
2020: 5, 2021: 2, 2022: 0, 2023: 4, 2024: 5, 2025: 5, 2026: 4. No
capture of `worldsdc.com` exists before 2016-10-30. Editions the
captures miss come from registry occurrences at month precision
(`docs/reference/backfill.md`). Other paths (`/event-list/`, `/print-event-list/`,
`/event-calendar/`, `/events-map/`) have captures in 2021 and 2022, the
2016 captures use a fourth date format, and before 2016-02 the list lived
at `swingdancecouncil.com/ActiveServerPages/UpcomingEvents.asp`; see
`journal/investigations/2026/event-list-sources-2026-09-12.md`. Gaps of more than about five months lose
short-lived listings (**unverified** gap size).

## 11. Load estimate

One request a day, 33 KB.

## 12. Operator switch

`User-agent: swingset` in `robots.txt`.

## 13. Open items

- Whether the Yoast sitemap's `lastmod` for `/events/` tracks calendar
  edits; if so it is a 304-capable change signal.
- Whether a WP REST endpoint exposes the calendar plugin's data.
