# Scheduling

## Watches

A watch is one row: `watch_id`, `source`, `kind` (index, autoindex,
event, round, pdf, json, site, dancer), `method`, `url`, `form` (canonical JSON, nullable), `archive_url` (nullable, for
backfill via the Wayback Machine), `parser`, `source_ref` (nullable), `state`,
`next_check_at`, `last_checked_at`, `last_changed_at`, `unchanged_streak`,
`etag`, `last_modified`, `body_sha256`, `paused_until`, `notes`.

Event context is resolved through `source_event_map`; watches never own
a second canonical event mapping. Additional persistence fields are in
[local state](state.md#sqlite-schema).

Watches are created by `discover` and by parsers (a parsed event page
creates round watches; a parsed round payload creates PDF watches).
The cheapest watch per event carries the fast timer: EEPro's
autoindex, scoring.dance's event page, WDR's rounds JSON, DCN's results
tab. Child watches (rounds, PDFs) are made due by their parent's change
and, because no parent signal we have is exact enough to reveal every
correction inside a child, by a slow clock of their own as well. The
child clock is set per host in the playbook: conditional GETs where the
host honors validators (EEPro), full fetches where it does not
(scoring.dance), a single end-of-cooling sweep where every fetch is
expensive (DCN). WDR has no children.

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

These are defaults. A playbook may override intervals for its host in
its section 6 (danceconvention.net does, because each poll is 1.67 MB).
| `archived` | after cooling | 90 days | Only to catch late corrections and link rot. |
| `gone` | 3 consecutive 404s | none | Kept for provenance. |
| `paused` | host or source paused | resumes at `paused_until` | Overrides the others. |

Index pages (EEPro event list, scoring.dance recent list, DCN upcoming
and archive, WSDC calendar) are always `live`-like with a fixed interval:
1 h from Friday 00:00 UTC to Monday 12:00 UTC, otherwise 6 h. The WSDC
calendar is once every 24 h.

Registry dancer watches follow [sources](sources.md#wsdc-registry-pointsworldsdccom), not this table.
After bootstrap, bounded new-id probes run daily when recent unlinked,
points-eligible individual Newcomer or Novice finalists could be waiting for
their first number, and weekly otherwise. Known finalists receive daily
refreshes while a recent eligible event result is absent, for at most 30 days
after each event. These policies support multiple overlapping events.

Backfill watches (historical events found in platform archives or the
Wayback CDX) have state `backfill`. When `archive_url` is set the fetch
goes to the Wayback Machine, not the origin. They are fetched only when no other watch is due in the
current run, newest event first, one page at a time within the normal
politeness rules. Once fetched they behave like `archived`. Backfill is
expected to take weeks and that is fine.

## Discovery

`discover` maintains watches from index pages:

1. Parse the WSDC calendar into observations and project `events` rows
   (series slug + month).
2. Parse each platform's index into observations and project `source_events` rows with the
   platform's own id, name, and dates. Archive indexes (EEPro year
   pages, scoring.dance sitemap, DCN `eventsarchive:loadyear`) are read
   once per year of history and produce `backfill` watches.
3. Project `source_event_map` by matching source observations to calendar
   events by normalized name and date overlap, then applying overrides.
   Unmatched source events still get an `events` row with
   `wsdc_status = "unknown"`. Ambiguous matches go to a review queue and
   are fixed by adding a row to `overrides/event_aliases.csv`.
4. Create or update the event watch for each source event.
5. Read `overrides/source_urls.csv` and create watches for its rows
   (World Dance Registry and long-tail events).
6. Event-site link scan. Each event's own website gets a `site` watch
   that is fetched every 24 h from the start of `upcoming` through the
   end of `cooling` (results links are often added during or after the
   event), then stops. It stops early once the event has a platform or
   WDR results watch, or an override row, and it never runs more than
   about 50 times per event. The scan looks for links to the platforms,
   to `scores.worlddanceregistry.com`, and to Drive folders. Platform
   and WDR links create watches; other hits become suggested override
   rows in the review queue. Event sites use the default host settings.

Discovery never deletes watches.


## Work order

Choose watches by priority, then `next_check_at`: live, cooling, index,
upcoming, archived, registry, backfill. The sweep sits below archived
work. Registry lookups use POST form data but otherwise use the same
watch, gate, archive, and recovery path. Source policy supplies registry
sweep, probe, trickle, and confirmation schedules.

The cycle drains existing downstream work before fetching another batch,
as [operations](operations.md#cycle) specifies. This is separate from
watch priority; pending parse and projection work is not another polling
state. Manual operator pauses are described in
[operations](operations.md#locks-and-operator-commands).
