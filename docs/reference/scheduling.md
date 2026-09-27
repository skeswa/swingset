# Scheduling

The scheduler chooses which request or local task runs next. This page defines polling and fairness rules. The event-completion extension is target behavior until its acceptance is recorded in [status](../status.md).

[Reference index](README.md)

## On this page

- [Watches](#watches)
- [Watch states and intervals](#watch-states-and-intervals)
- [Discovery](#discovery)
- [Work order](#work-order)
- [Loops](#loops)
- [Event completion](#event-completion)
- [Registry verification consumers (H2)](#registry-verification-consumers-h2)

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

Registry dancer watches follow [sources](sources/wsdc-registry.md), not this table.
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
turns before fresh arrivals can displace them. The local implementation exposes
all eligible candidates from the finite retained historical plan to that
rotation together. Offering candidates creates no watches or request debits;
dispatch repeats the year, source, parent, and capture gates for the selected
page. A watch whose event ended
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
limit or the 60-second write alarm. A zero budget still accepts
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
a blocked attempt on its own. Every failure gets a clock from the work queue
([D-0216](../../journal/decisions/0216-the-work-queue-leases-backs-off-and-quarantines.md);
[state: work queue](state.md#work-queue)). A derivation that raises retries
after 15 minutes, doubling per consecutive failure of one class up to six
hours, and each retry counts as a failure in the cycle summary; the third
quarantines the unit for seven days (the twentieth for `unit_io_error`), and
the watchdog's `[unit-quarantined]` names it. A blocked parse or a missing
artifact waits 24 hours doubling to seven days, then retries; a retry that
ends the same way counts in `waiting_repeats`, not as a failure, and not in
the per-source 10% rule. A unit another live owner leases is skipped
(`queue.lease_busy`), as is one whose admission timed out on the control lock
(`control_timeouts`). The cycle wakes due queue rows once (`queue.woken`),
releases every lease after its recovery (`queue.released`), names units it
quarantined (`queue.quarantined`) and reports rows by state (`queue_states`).
The summary's `derivation` key reports generations and equivalences
written, by `stage/kind`, and the replay epochs a cutoff moved. If that read,
the replay-epoch read at the start of the cycle, or the controls read fails,
the record keeps `derivation_error` or `controls_error` instead, and is still
written; without a starting read the replay bumps are left out. The watchdog's
`[report-failing]` names a record with either key or without `derivation`.
`current_hints_dropped` counts project and link hints offline selection found
current and deleted; a failed delete is noted as `current_hints_error`, does
not fail the cycle, and no watchdog line reads it yet. A kind whose
recipe cannot be formed is skipped and named in `readiness_errors`, which
fails the cycle. Once offline work settles, saved interpretation and a
build receive offline time before unused time is lent back to collection.
Cycle receipts report actual phase time separately from configured shares.

Acquisition pauses when pending parse work reaches 1,000 items or 128 MiB
of distinct retained bodies, or pending work reaches 10,000 items. With
derivations that count is parse rows only: project and link rows are hints
that code-scope accepts and replay epochs queue for the watchdog, and a
derivation backlog does not pause acquisition
([D-0214](../../journal/decisions/0214-a-derivation-backlog-does-not-pause-acquisition.md)).
Only parse units derive could run now count, by items and by bytes: no queue
row, a lease, or `ready` and due. A parse waiting on a clock (backing off,
waiting for input, quarantined) does not hold acquisition
([D-0216](../../journal/decisions/0216-the-work-queue-leases-backs-off-and-quarantines.md));
the report's `runnable_parse` gives the count.
Two actual requests per cycle remain available for explicitly identified
identity dependencies. Ordinary unfetched rounds do not receive that
reserve. Robots, redirects, retries, and failed issued requests consume
it. One response can cross a threshold; these are admission high-water
marks, not hard limits on an unknown response size. Offline work continues.

`[scheduling]` in `config/sources.toml` configures cycle shares, the service
gap, high-water marks, repair request count, and watch recovery clocks. Two
settings pace the units that read the whole graph: `build_interval_seconds`
admits a release build at most that often, failed or not, and
`history_interval_seconds` re-materializes `project/history/all` at most that
often. Production sets 6 and 12 hours; unset means every cycle
([D-0199](../../journal/decisions/0199-contain-build-failures-and-pace-global-units.md)).
`release_min_free_bytes` (default 30 GB) is the free disk the release loop
needs before it builds.
`inventory_interval_seconds` (12 hours) paces `project/inventory/all` the same
way. A due history lifts it only once history has consumed the inventory it
has; otherwise a dancer change during the cascade would restart it. With equal
intervals that never happens, so the inventory runs at most twice a day,
plus once per calendar change below. A
history whose latest attempt failed does not count as due. An inventory made
at or after history came due started a cascade that a dancer change then
interrupted; from then on the inventory runs whenever it is stale until
history lands, so a dancer change in every cascade cannot starve history
([D-0210](../../journal/decisions/0210-pace-the-inventory-like-history.md)).
A calendar replay the inventory has not read (its epoch is newer than the
highest one the inventory's current output recorded, over its generation and
every equivalence) lifts the wait as well, and so does a calendar generation
newer than the inventory's output (except `wsdc-history`, which writes no
rows): either rewrote live `events` columns the inventory enriched.
Each new inventory re-mints the map, every event, history and every link. Both
intervals start at the newer of the kept generation's time and its latest
cutoff (`derivations.materialized_at`). The rule lives in
`schedule/pacing.py`. The cycle names its scheduler there, so the phase-two
gate and `accept-year` treat a held inventory (`pacing.holds`: not current and
made within its interval), and the map behind it, as not pending and read the
committed event list (I5). A current inventory holds nothing, so a pending
map behind it counts as pending. The cycle summary's
`phase_two_refusals` counts that gate's refusals by reason.
Changing these settings captures a new input bundle without relabeling
source interpretations. The measured load and the distinction between
observations and initial objectives are in
[the H14 shadow report](../../journal/investigations/2026/h14-shadow-load-2026-09-13.md).

Dateless watches get three metadata recovery attempts, then a 30-day
recheck. Expected unpublished results retain their bounded daily window;
persistently unavailable watches move to a 90-day clock. Repeating the
same parent link does not reset these clocks. A newly observed parent-child
relationship or actual recovered dates can reactivate the watch without
inventing dates. Manual operator pauses remain separate, as described in
[operations](operations.md#locks-and-operator-commands).

## Loops

Phase 2 splits the cycle into loops, each a short timer tick that holds
`state.lock` shared and its own `loop-<name>.lock`
([D-0215](../../journal/decisions/0215-loops-share-the-state-under-a-reader-writer-lock.md);
[state: locks](state.md#locks)). All five are built; the cycle stays the
default until the nix switch. The cycle and
derive share one offline loop (`schedule/offline.py`), and the cycle, plan and
acquire share the acquisition pass and the event bookkeeping stages
(`schedule/acquisition.py`), so both orchestrations select, attempt and count the
same way and the cycle's summary is unchanged.

Every tick (`loops/runtime.tick`):

1. Opens the state shared. A busy loop lock, a waiting or running writer, or a
   migration another process won skips the tick (`skipped-overlap` under
   `--timer`, exit 1 by hand).
2. Settles the active admissions of this loop's open runs: the loop lock
   proves them dead. Release also settles a stale publication as uncertain.
3. Skips when a publication is in flight, or when one admitted since fences
   the run's start: its fence refuses every write, `runs` and `loop_state`
   included (F6). The record is only `runs/<run_id>-<loop>.json`, with
   `skipped: publication_in_flight`.
4. Starts a `runs` row with `loop` set and closes the loop's other open runs:
   `failed`, `process_interrupted`, unless the run's record file says it
   finished (its last row write met a fence). Their running attempts finish
   `interrupted`. A run that died counts a `process_interrupted` failure of
   each such unit, which backs off and quarantines at the third; a run that
   finished on its own left the attempt behind a fenced or busy write, which
   retries in 60 s uncounted ([state: work queue](state.md#work-queue)).
   A lease the dead run holds with no running attempt is a death in the
   unit's admission and counts the same. Nothing counts when the run wrote
   `runs/<run_id>.stop` before it died: systemd asked it to stop (SIGTERM)
   and killed it at `TimeoutStopSec`. A record file that says
   `process_interrupted` was written by a recovery that rolled back and is
   no ending. The other leases are released.
5. Captures the input bundle. Only derive accepts it; plan and release skip
   (`inputs_not_accepted`, guard `loop:<name> inputs_not_accepted`, 1 h)
   until the accepted bundle matches.
6. Leases as `<loop>:<run_id>`, counts phase-two refusals, and, for plan and
   derive, names the pacing gate's scheduler. Acquire, plan and derive judge
   the phase-two year gate on facts alone, never on a pending projection
   ([D-0218](../../journal/decisions/0218-the-loops-judge-the-year-gate-on-facts-and-acquire-from-a-plan-table.md);
   [backfill: scheduling](backfill.md#scheduling)).
7. Writes `runs/<run_id>.json`, the `runs` row and `loop_state`
   (`last_progress_at` moves only when the tick made progress). The record
   carries `loop`, `counters`, `progress`, `failed`, `error`, `stopped`,
   `skipped`, `duration_seconds`, `control_timeouts`, `peak_rss_bytes` (the
   process's peak resident memory) and the write counters.
   The CLI exits 1 when `failed`.

| Loop       | Tick                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                | Progress                                                                           |
| ---------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------- |
| `acquire`  | Discovers configured and registry watches, then runs one acquisition pass to its deadline: due watches and the eligible `acquisition_plan` page rows, in plan order, compete under the fair policy; each history dispatch re-checks its target from facts before any mutation. A row whose page left the retained plan waits for the next plan tick (`stale_plan_rows`). Under backpressure no row is offered and the archive index is skipped, as in the cycle. The event-timing observer wraps the pass but gets no archive offer proofs, since the rows were judged in an earlier tick. Flushes the pass's guards. It stops before the next request once a publication starts. A choice that raises stops only itself (`request_errors`): a busy or locked write, a write-deadline overrun, a publication fence, an operator pause or a control-lock timeout skips the request; any other exception holds its watch (guard `watch:<id> request_error`) for an hour, doubling, and a week from the third in a row, and a request that goes through clears it. The archive index and the guard flush still run. `failed` on a stage error, a request error other than a skip, or a `Blocked` host. | requests issued (fetches plus CDX pages)                                           |
| `plan`     | Runs the event bookkeeping stages as the cycle does (`[stages]` switches apply), the requirements scan and the registry verification summary. Then writes `acquisition_plan`: one row per `events.year` (`eligible`, `year_unaccepted`, `acceptance_digest_changed`, `year_findings_open` or `h7_h10_not_deployed`; `detail_json` holds the accepted digest and, as information only, `projection_pending`) and one per planned page (`eligible` or the candidate's stop reason, with `rank` in plan order and `via` archive or origin). Rows are upserted in transactions of 5,000; year rows not written are deleted, and page rows not written only when the pass was not cut by its deadline (30 s are kept for the write).                                                                                                                                                                                                                                                                                                                                                                                                                                                                     | plan rows written                                                                  |
| `derive`   | Wakes due queue rows, enforces the size cap, rebuilds parse hints (2 s), then runs the offline loop to its deadline. Without the heavy lock (release is building) it starts no unit (`heavy_deferred`): a build and a parse tick together outgrew the 8 GB worker on the Mac (F10). It stops before the next unit once a publication starts. The registry crosscheck runs, with the heavy lock, when no parse or project unit waits. An exception that escapes a unit fails that unit (`unit_exception:<type>`), counted. `failed` is the cycle's rule without the build and host terms.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            | units whose output committed; other outcomes count under `not_completed` by reason |
| `release`  | Skips while `/run/swingset-disk` shows under `release_min_free_bytes` free (`disk`; guard `loop:release release_disk_low`, cleared by the next tick with room). With a hub (`--publish`), reconciles a pending publication, then builds a correction while work is queued and the baseline needs one. Builds a candidate unless `build_interval_seconds` or the backlog rule defers it (`build_deferred`). Each build holds `heavy.lock` exclusive, waiting up to 15 minutes, else skips (`heavy_busy`, guard `loop:release heavy_busy`, 3 h); meanwhile derive starts no unit. A `/run/swingset-disk` reading older than an hour is unknown (`disk.stale`). The record carries `build_attempted` and `build_interval_seconds` for the watchdog. Publishes with `--publish`. A `BuildError` or `ClosureError` is reported (`build_error`, `correction_error`) and fails the tick without raising ([D-0187](../../journal/decisions/0187-review-dismissals-year-scoped-aliases-and-non-fatal-builds.md), [D-0199](../../journal/decisions/0199-contain-build-failures-and-pace-global-units.md)). The build and publish steps are the cycle's (`schedule/cycle.build_and_publish`, `correct`).       | a build, a publication or a landed reconcile                                       |
| `maintain` | `wal_checkpoint(PASSIVE)` and the WAL size; deletes guard rows unseen for 7 days, idle loop records (no progress, not failed) and stop markers older than 14 days, and every loop record after 30 days; compacts the `summary_json` of loop `runs` rows older than 30 days to a stub (loop, failed, progress, finished_at), 5,000 a tick; drops `work_queue` rows whose unit has nothing left to do ([state: work queue](state.md#work-queue)); counts building generations, those untouched for 24 hours, and partial generations no building row names (`incomplete_generations`); reports the size cap, controls and guard reasons.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              | each task done                                                                     |

## Event completion

Accepted extension, 2026-09-13. Retained enumeration, bounded event turns,
protected listed-page capacity, and a local artifact drill-down are implemented
in the working source. Conservative expansion controls, blocker history, and
bounded progress observations and sampled local accounting transitions are also
implemented locally. Immediate-edge page retirement proofs are also implemented;
whole-event retirement, complete retirement history, eligible-time reporting, and
operating acceptance remain pending.
Existing H14 class fairness alone does not satisfy
this extension. The
[Monterey investigation](../../journal/investigations/2026/monterey-missing-data-2026-09-13.md)
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

The first implementation uses a four-request target, configurable through
`event_turn_requests` from 1 to 64. An already selected fetch may finish its
bounded redirect, retry, and robots chain, which can issue up to 80 requests.
The absolute turn bound is therefore `target + 79`, or 83 at the default.
Every actual request consumes the turn and its actual host's existing debit.
Rotation happens before the next fetch selection. These are unmeasured shadow
defaults, not accepted operating guarantees; see
[D-0010](../../journal/decisions/0010-bounded-event-turns.md).

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
The working implementation starts `listed_page_percent` at 50, configurable
from 1 through 99. Durable credit accounts for actual issued requests while
both sides compete; borrowing creates no new repayment debt. The target is
unmeasured and can deviate over one bounded fetch chain. It does not change
which watches belong to the existing new-work class. See
[D-0012](../../journal/decisions/0012-protect-listed-page-capacity.md).
Index discovery and result acquisition cannot both claim the same debit.
Retain essential platform discovery and current-event checks under their
existing class allowances. When unfinished source events exceed a configured
watermark, defer discretionary event-index expansion; do not delete its watches
or stop processing links from an already acquired index. When the count falls
below the watermark, expansion becomes eligible again.

Production turns this gate off by config; see [Stage switches](#stage-switches).
The local implementation observes started source events per request host. An
unfetched event index alone does not count as started. The initial thresholds
defer additional event indexes above 100 unresolved events and reopen below
80; between them, the previous decision survives. These proposed values have
not received operating acceptance. Essential discovery and started-event
continuations still use their ordinary request gates.

Each cycle can refresh eight events, at most 32 distinct page requests per
probe. Checks bound returned rows, JSON, artifact bytes, and cooperative elapsed
time. Partial, expired, changed, unsupported, or oversized observations retain
pressure. Observations expire 24 hours after the earliest contributing check;
restoration invalidates them before activation. No observation grants stage
completion or publication. Doctor exposes the stored observations and gate
decision without refreshing them.

An oversized event may remain unresolved indefinitely. Historical host
associations also remain, so moving a URL may over-defer its old host. SQL and
filesystem calls have no hard deadline. See
[D-0018](../../journal/decisions/0018-gate-expansion-with-conservative-observations.md)
for the bounds and limitations.

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

The local implementation reconstructs missing parse hints for currently
declared member watches whose selected successful snapshot remains pending.
It consults retained attempts and same-unit admission receipts, preserving
terminal decisions and retry deadlines. A failed parse requires an explicit
retry newer than its latest own attempt before its hint can be reconstructed.
The cycle scans at most 100 candidates
within a shared 2 MiB metadata allowance and a cooperative two-second deadline.
A frozen snapshot-row high-water and disposable cursor bound each pass.
Reconstruction reads no artifacts and starts no operation; normal pause and
retry gates still govern execution. It does not establish global event-stage
absence, and undeclared normalized aliases remain outside this initial scan.
Implementation and acceptance evidence are tracked in
[current status](../status.md).

Persist rotation and issued service with the existing request accounting;
recovery cannot refund issued requests or grant a fresh turn by restarting.
Progress commits with the stage output it describes. Budget resets and resume
preserve pending membership, turn position, cooldowns, and completed work.
Operator holds remain effective and earn no extra requests or accumulated burst.

Each acquisition pass ends by writing its guards: why the client held a fetch,
by host, watch or loop, with an expiry. A request clears its host's and
watch's rows. The cycle and acquire summaries carry `guards` (passes, rows
written and cleared) and `guard_reasons` (rows by reason). Rules and expiries:
[fetch layer, guards](fetching.md#guards).

### Stage switches

The top-level `[stages]` table of `config/sources.toml` turns optional event
bookkeeping off. Every key defaults to `true`. An unknown key or a value that is
not `true` or `false` stops config loading. The plan loop runs these stages
under the same switches.

| Key                          | When `false`                                                                                                                                                         |
| ---------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `event_site_review`          | The cycle skips the event-site review and its `history_acceptance` probe.                                                                                            |
| `event_pressure`             | The cycle skips pressure bootstrap and refresh. The gate opens: no `event_index` watch is denied, and starts, request debits and event turns write no pressure rows. |
| `event_progress_refresh`     | The cycle skips the progress refresh.                                                                                                                                |
| `event_blocker_observations` | The cycle skips blocker observations.                                                                                                                                |

A skipped stage starts no operation and leaves no key in the cycle summary. The
summary's `stages_disabled` lists the keys set to `false`. Pressure must open
its gate when off: nothing then refreshes observations or drains dirty rows, so
stale subjects or pending metadata would close expansion for good. Doctor and
the event report show the pressure gate as `disabled`. Stored pressure,
progress and blocker rows stay as they were. Two writers do not stop: the
migration 0019 triggers still mark a changed watch or snapshot dirty in
`event_pressure_dirty` (at most one row per watch; a re-bootstrap drains
them), and a fetched or parsed snapshot still appends an
`event_stage_operations` row, so that table keeps growing with snapshots.
Only pressure gates acquisition; the other three feed reports and
diagnostics.

Production sets all four keys to `false`
([D-0204](../../journal/decisions/0204-turn-off-the-event-bookkeeping-stages-and-open-the-pressure-gate.md)),
so no bookkeeping stage runs before acquisition and nothing they write gates
it. The code default stays `true`. The table takes effect with the phase 1
package; the 019 package ignores it, and all four stages run until then.
Turning `event_pressure` back on, by config or by rolling the package back to
019, needs no command. The cycle's pressure bootstrap drains
`event_pressure_dirty` 100 watches a cycle, and `metadata_pending` defers
new-event admission on every host until the dirty event watches drain. Each
drained result or event-index watch with a stored page, or a declared result
obligation, is enrolled as started, so every such page fetched while pressure
was off is counted. A request that stored no page while it was off is not,
until that watch is requested again; until then the gate undercounts those
starts and can admit a few too many events. On the 2026-09-25 16:10 UTC copy
about 1,900 watches got new snapshots per week, so expect about 5 hours of
closed expansion per week the stage was off. Hosts whose subjects went stale
can then stay latched high until the refresh, eight events a cycle, catches
up. The watchdog does not see either, so watch `event_index` requests by host
for a day after turning pressure back on.

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

The current local observation slice retains changes in sampled blocker facts
and per-refresh counts. It rotates through eight events with 64-member and
64-watch bounds. Oversized events remain unassessed. Diagnostic bookkeeping can
record a source or global pause without starting paused source work. The last
saved change is not proof that its conditions held continuously afterward;
eligible service age remains unknown. Bounded ordinary-acquisition timing is
now implemented locally under the [timing contract](event-timing.md). Historical
dispatch, interpretation waiting and whole-event eligible age remain outside
that measured scope. See
[D-0019](../../journal/decisions/0019-retain-observed-event-blocker-changes.md).

For a fixed set of eligible events and sufficient allocated capacity, fake-clock
tests must demonstrate service within a declared finite bound and eventual
completion of retrievable, supported pages. Continuous new arrivals cannot
displace existing waiting events. This is conditional on host availability,
controls, and stage support; it is not an unconditional wall-clock deadline.
The full scenarios and staged rollout are owned by
[H14's extension](../plans/recovery/README.md#event-completion-extension).

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
