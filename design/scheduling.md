# Scheduling

## Watches

A watch is one row: `watch_id`, `source`, `kind` (index, event, round,
pdf, dancer), `url`, `parser`, `event_id` (nullable), `state`,
`next_check_at`, `last_checked_at`, `last_changed_at`, `unchanged_streak`,
`etag`, `last_modified`, `body_sha256`, `paused_until`, `notes`.

Watches are created by `discover` and by parsers (a parsed event page
creates round watches; a parsed round payload creates PDF watches).

## Watch states and intervals

Windows are computed from the event's start and end dates. We do not need
the event's time zone: we pad in UTC. `live` starts 36 hours before
`start_date 00:00 UTC` and ends 48 hours after `end_date 00:00 UTC`.
This covers Thursday-night contests and Sunday-night postings anywhere
on Earth at the cost of a few extra cheap conditional GETs.

| State | When | Interval | Notes |
|---|---|---|---|
| `dormant` | more than 14 days before start | none | Watch exists, nothing fetched. |
| `upcoming` | 14 days to 36 h before start | 24 h | Catches early postings and schedule changes. |
| `live` | see above | 15 min, plus 0 to 5 min random jitter | Conditional GET. Doubles after 8 unchanged checks in a row, up to 1 h. Resets to 15 min on any change. |
| `cooling` | end of live to end + 30 days | 6 h, doubling per unchanged check up to 24 h | Catches corrected results. Resets to 6 h on change. |
| `archived` | after cooling | 90 days | Only to catch late corrections and link rot. |
| `gone` | 3 consecutive 404s | none | Kept for provenance. |
| `paused` | host or source paused | resumes at `paused_until` | Overrides the others. |

Index pages (EEPro event list, scoring.dance recent list, DCN upcoming
and archive, WSDC calendar) are always `live`-like with a fixed interval:
1 h from Friday 00:00 UTC to Monday 12:00 UTC, otherwise 6 h. The WSDC
calendar is once every 24 h.

Registry dancer watches follow [sources](sources.md#wsdc-registry-pointsworldsdccom), not this table.

Backfill watches (historical events found in platform archives) have
state `backfill`. They are fetched only when no other watch is due in the
current run, newest event first, one page at a time within the normal
politeness rules. Once fetched they behave like `archived`. Backfill is
expected to take weeks and that is fine.

## Discovery

`discover` maintains watches from index pages:

1. Parse the WSDC calendar into `events` rows (series slug + month).
2. Parse each platform's index into `source_events` rows with the
   platform's own id, name, and dates. Archive indexes (EEPro year
   pages, scoring.dance sitemap, DCN `eventsarchive:loadyear`) are read
   once per year of history and produce `backfill` watches.
3. Match `source_events` to `events` by normalized name and date overlap.
   Unmatched source events still get an `events` row with
   `wsdc_status = "unknown"`. Ambiguous matches go to a review queue and
   are fixed by adding a row to `overrides/event_aliases.csv`.
4. Create or update the event watch for each source event.

Discovery never deletes watches.
