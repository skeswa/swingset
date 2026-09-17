Full live body fetched once through swingset on 2026-09-09T03:50:00.329348+00:00.

Source: https://worldsdc.com/events/

SHA-256: `cd10a8f5cc463e0a73927cc48667fa9fbf9abcf08e5a4847220757f1162150c6`. The expected file contains source observations in row order.

Historical body `calendar-20161113.html` was fetched through the persistent
phase1 gate on 2026-09-13 UTC. Its neighboring JSON retains snapshot metadata.
Source: https://web.archive.org/web/20161113141128id_/http://www.worldsdc.com:80/events/
The page has 20 initial table rows and 127 full event cards, including 2018
listings. The parser uses all 127 cards and avoids double-counting the table.

`calendar-20210415.html` and its neighboring JSON retain the 2021-04-15
FullCalendar capture fetched through the same gate on 2026-09-13 UTC. Its
500 inlineJSON events span 2018–2023. The parser readsJSON without running
JavaScript. FullCalendar's all-day `end` is exclusive, so one day is
subtracted to recover the displayed final day; see the official
[FullCalendar v3 Event Object](https://legacy.fullcalendar.io/v3/event-object).
Source: https://web.archive.org/web/20210415144728id_/https://www.worldsdc.com/event-calendar/

`calendar-2016-literals.html` retains the earlier string-only JavaScript
object-array shape; `calendar-2019-map.html` retains 105 dated map markers.
Both were fetched through the gate on 2026-09-13 UTC; adjacent JSON records
the exact replay URL and Memento time. Neither parser executes scripts.
Registry/trial/member labels come from printed titles or named marker
icons. Unknown activity labels remain unknown in the canonical model.

`calendar-2017-single-dates.html` is the original 2017-01-24 map capture
retained through FetchClient on 2026-09-13 UTC. Its adjacent JSON records
the full snapshot provenance. Nine markers print a single calendar date;
parser 7 preserves that date as both endpoints without inventing duration.
The Chicago Classic marker retains its printed HIATUS label.


`calendar-map-20160710`, `calendar-map-20160909`, `calendar-map-20161110`
and `calendar-map-20161212` are complete previously acquired production bodies,
copied read-only on 2026-09-17 with adjacent original snapshot metadata. Their
body hashes were verified after decompression. All marker popups contain names
and websites only, without printed dates. These remain unsupported dating
controls; capture timestamps are not event dates. See the
[inspection](../../../../../journal/investigations/2026/calendar-map-gaps-2026-09-17.md).
