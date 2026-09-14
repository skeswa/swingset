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

| State      | When                           | Interval                                     | Notes                                                                                                  |
| ---------- | ------------------------------ | -------------------------------------------- | ------------------------------------------------------------------------------------------------------ |
| `dormant`  | more than 14 days before start | none                                         | Watch exists, nothing fetched.                                                                         |
| `upcoming` | 14 days to 36 h before start   | 24 h                                         | Catches early postings and schedule changes.                                                           |
| `live`     | see above                      | 15 min, plus 0 to 5 min random jitter        | Conditional GET. Doubles after 8 unchanged checks in a row, up to 1 h. Resets to 15 min on any change. |
| `cooling`  | end of live to end + 30 days   | 6 h, doubling per unchanged check up to 24 h | Catches corrected results. Resets to 6 h on change.                                                    |

These are defaults. A playbook may override intervals for its host in
its section 6 (danceconvention.net does, because each poll is 1.67 MB).
| `archived` | after cooling | 90 days | Only to catch late corrections and link rot. |
| `sealed` | event ended more than two years ago, or the origin is dead, after a successful parse | none | Never refetched; reparse from the archive still works. See [backfill](backfill.md#scheduling). |
| `gone` | 3 consecutive 404s | 90 days | Kept for provenance; a genuinely new parent relationship can reactivate it. |
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
goes to the Wayback Machine, not the origin. An eligible historical offer
receives the old-work acquisition share, one page at a time within normal
politeness rules and year-acceptance gates. New events enter newest first;
under the event-completion extension, already-waiting events receive bounded
turns before fresh arrivals can displace them. A watch whose event ended
more than two years ago, or whose origin is dead, becomes `sealed` after
a successful parse and is never fetched again; younger ones behave like
`archived`. Order, capture selection, and origin fallback are owned by
[backfill](backfill.md). Backfill is expected to take weeks and that is
fine.

## Discovery

`discover` maintains watches from index pages:

1. Parse the WSDC calendar into observations and project `events` rows
   (series slug + month).
2. Parse each platform's index into observations and project `source_events` rows with the
   platform's own id, name, and dates. Historical indexes (archived
   calendar captures, archived platform indexes, the Step Right events
   index, DCN `eventsarchive:loadyear`, registry occurrences) are read
   as [backfill](backfill.md#event-enumeration-for-history) describes
   and produce `backfill` watches and registry-seeded events.
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

Discovery never deletes watches. Discovering a results link creates a pending
request; it does not establish that the sheet has been acquired or interpreted.
The event's [completion inventory](#event-completion) survives changes to its
parent's polling state.

## Work order

Reconciliation and supported corrections run first. The initial 720-second
cycle reserves 30 seconds for reconciliation, 300 for acquisition, and 390
for offline work, including building. Unused allocations can be borrowed.
These shares are operating objectives, not permission to exceed a host
limit or the 45-second semantic write deadline. A zero budget still accepts
inputs and starts no work.

Acquisition divides each host's existing request allowance between new,
current, identity, and old eligible demand. Initial percentages are:

| Host                | New | Current | Identity | Old |
| ------------------- | --: | ------: | -------: | --: |
| scoring.dance       |  50 |      25 |       10 |  15 |
| points.worldsdc.com |  10 |       0 |       70 |  20 |
| web.archive.org     |  50 |      10 |       20 |  20 |
| Other hosts         |  40 |      30 |       20 |  10 |

The picker uses actual issued requests divided by class weight. Empty
classes lend their share; host service rotates independently. A class
without service for 24 hours is promoted when it is eligible. This is a
class-service objective, not a per-watch completion deadline. The
[event-completion extension](#event-completion) adds service within each class.
Pause time
does not earn credits or extra capacity. Existing daily usage remains
charged after restart, resume, or a policy change. Historical acquisition
requires the dispatcher's year, parent, capture, and admission checks;
an existing archive watch does not bypass those checks. The four exact
phase-one event-list kinds retain their inventory exception.

Within offline work, durable service sequence rotates both stage and unit
kind. A continual parse or mapping backlog cannot consume every turn.
The existing unit ordering, retry fingerprint, deadline, and control scope
still determine eligibility. Service records an attempted unit; it never
claims that an output committed successfully.

A cycle excludes an already attempted input fingerprint, rather than the
whole unit. Later parses can add inputs to a shared projection and let it
run again in that cycle. Repeating the same bytes and recipe does not unlock
a blocked attempt. Once offline work settles, saved interpretation and a
build receive offline time before unused time is lent back to collection.
Cycle receipts report actual phase time separately from configured shares.

Acquisition pauses when pending parse work reaches 1,000 items or 128 MiB
of distinct retained bodies, or total pending work reaches 10,000 items.
Two actual requests per cycle remain available for explicitly identified
identity dependencies. Ordinary unfetched rounds do not receive that
reserve. Robots, redirects, retries, and failed issued requests consume
it. One response can cross a threshold; these are admission high-water
marks, not hard limits on an unknown response size. Offline work continues.

`[scheduling]` in `config/sources.toml` configures cycle shares, the service
gap, high-water marks, repair request count, and watch recovery clocks.
Changing these settings captures a new input bundle without relabeling
source interpretations. The measured load and the distinction between
observations and initial objectives are in
[the H14 shadow report](../research/h14-shadow-load-2026-09-13.md).

Dateless watches get three metadata recovery attempts, then a 30-day
recheck. Expected unpublished results retain their bounded daily window;
persistently unavailable watches move to a 90-day clock. Repeating the
same parent link does not reset these clocks. A newly observed parent-child
relationship or actual recovered dates can reactivate the watch without
inventing dates. Manual operator pauses remain separate, as described in
[operations](operations.md#locks-and-operator-commands).

## Event completion

Accepted extension, 2026-09-13; implementation and operating acceptance are
pending. Existing H14 class fairness does not satisfy this extension. The
[Monterey investigation](../research/monterey-missing-data-2026-09-13.md)
found 33 discovered round pages with no fetch attempt. This contract makes
finishing discovered work a scheduling objective without increasing host limits.

### Inventory and completion

Group work by `(source, source_ref)`, before canonical event matching. A typo,
ambiguous alias, or missing registry connection must not prevent an otherwise
eligible source request. Historical year and source-admission gates still apply.
The existing matching map remains the sole owner of canonical event identity.

An event enumeration pins the admitted parent snapshot or snapshots, their
interpretation generation, and the distinct listed page requests. Pagination
must finish before the enumeration is labeled complete. An incomplete index
still establishes a finite set of known links; it does not establish that all
links are known. An event payload containing results directly can satisfy work
without child requests. Count request pages separately from rounds: one page
can contain several rounds, and a round can require several pages.

Derive acquired, interpreted, and release-represented membership from retained
artifacts, admitted interpretations, and acknowledged release support. Report
mapping and identity blockers separately. No independent `complete` flag can
override those facts. A missing or corrupt artifact reopens the affected stage.
An unchanged poll, retry, scheduling turn, or inventory scan is not completion
progress. A supported unavailable or unsupported outcome accounts for a gap;
it does not count as an acquired, interpreted, or published result.

A changed index creates a new enumeration. Preserve the previous denominator,
show added and removed members, reuse still-valid evidence, and retain the
waiting age of existing work. Removed links require the source's admitted
removal authority; a smaller or failed index cannot silently erase obligations.
All known pages accounted for is not a claim of complete competition history.

### Selection and protected capacity

Selection follows host allowance, work class, source event, then missing page.
Keep the existing host and class fairness. Within an eligible class, events
receive bounded turns measured in issued HTTP requests. Rotate among events
after each turn and persist the position across cycles and restarts. Continuing
an event within its turn reduces scattered partial coverage; a large event
cannot keep the host until it finishes. New arrivals join behind already
waiting events. Skipped or blocked events keep their place for future eligible
service, but do not stop the current rotation. No preference for small events
may indefinitely postpone a large one.

Within new work, reserve a positive share for acquiring already-listed result
pages. Discretionary expansion into additional event indexes uses the remaining
share. Either side can borrow capacity when the other has no eligible work.
Index discovery and result acquisition cannot both claim the same debit.
Retain essential platform discovery and current-event checks under their
existing class allowances. When unfinished source events exceed a configured
watermark, defer discretionary event-index expansion; do not delete its watches
or stop processing links from an already acquired index. When the count falls
below the watermark, expansion becomes eligible again.

All eligible listed pages receive turns regardless of whether their event is
recent, partly acquired, or never started. Historical offers join the old-work
rotation only after the backfill dispatcher admits them. Its year, capture,
archive-first, and per-host event limits remain effective. Newest-first order
chooses among newly admitted historical events; it cannot repeatedly displace
an older event already waiting for its turn.

The implementation must capture positive, bounded event-turn sizes, the listed
page share, the unfinished-event watermark, and event service-gap objectives
in scheduler policy. Initial values require shadow measurement against the
retained backlog and finite fake-clock acceptance tests; they are not yet
measured operating guarantees. Shares divide existing allowances. Retries,
redirects, robots checks, and failed issued requests consume the same turn and
host debit. A shared request is charged to one selected event; its evidence
can advance every enumeration that references it. The fetch gate rechecks all
limits and controls before each request, even within a turn.

### Failure, pause, and recovery

A failed page retains its evidence, outcome, and next eligible action or retry
time. Continue with independent pages and events. A fully blocked event uses
no request allocation until work becomes eligible. Parsing and publication
failures remain visible without forcing successful pages to be downloaded again.

An archived, sealed, gone, or unchanged parent never clears unfinished child
work. Reconstruct lost queue hints from admitted enumerations and retained
parent relationships without making a new source request or resetting retry
delays. Explicit retirement remains visible and is not successful completion.

Persist rotation and issued service with the existing request accounting;
recovery cannot refund issued requests or grant a fresh turn by restarting.
Progress commits with the stage output it describes. Budget resets and resume
preserve pending membership, turn position, cooldowns, and completed work.
Operator holds remain effective and earn no extra requests or accumulated burst.

### Reporting and acceptance

For each source event, report the enumeration and completeness label, stage
counts, first discovery, last successful progress, missing pages, current
blockers, and next eligible action. Keep wall age and eligible service age
separate. Pause, exhausted budget, host cooldown, retry delay, and downstream
backpressure must be distinguishable. Legacy eligible age is unknown unless
the transition history supports it; migration time is not first discovery.

Record why a turn was selected and the policy, enumeration, and service position
used. Retain blocker transitions and per-cycle summaries rather than writing
one skipped-work record for every page on every scan. These receipts must
explain an event's wait within the recorded interval. Repeated failures may
consume service but must not reset a no-progress alarm. An operator hold
suppresses eligible-work alarms while wall age and the hold remain visible.

For a fixed set of eligible events and sufficient allocated capacity, fake-clock
tests must demonstrate service within a declared finite bound and eventual
completion of retrievable, supported pages. Continuous new arrivals cannot
displace existing waiting events. This is conditional on host availability,
controls, and stage support; it is not an unconditional wall-clock deadline.
The full scenarios and staged rollout are owned by
[H14's extension](self-healing-rollout.md#event-completion-extension).

## Registry verification consumers (H2)

Sweep and probe completion consume usable verification outcomes. A probe
requires a usable check strictly after its start; an identical response can
finish it without a new claim snapshot. A failed or still-uninterpreted check
receives a retry within 15 minutes instead of the annual profile interval;
parse failure backoff and host gates still apply. Discovery preserves a
current attempt's retry delay.

The daily 100-profile trickle selects missing or oldest usable verification
first, with a 365-day stale boundary. Successful identical checks rotate
profiles out of that selection without changing `registry_fetched_at`.
Weekly new-ID discovery continues after the 30-day intensive window. A new
projected dancer invalidates links for all retained events, including old
unresolved entries with no candidate-index edge.
