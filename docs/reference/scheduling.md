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
`archived`. Order, capture selection, and gaps are owned by
[backfill](backfill.md); the loops make no origin request from the history
plan ([D-0236](../../journal/decisions/0236-origin-candidates-and-event-site-review-are-offline-reports.md)). Backfill is expected to take weeks and that is
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
3. Map each source's listings to events, one `project/mapping/<source>`
   unit per source: an override, else the one calendar event with the same
   normalized name and overlapping dates. Unmatched listings still get an
   `events` row with `wsdc_status = "unknown"`. One
   `project/resolution/<event_id>` unit per event writes its
   `source_event_map` rows and that placeholder row
   ([state](state.md#mapping-per-source-resolution-per-event-plan-f19)).
   Ambiguous matches go to a review queue and are fixed by adding a row to
   `overrides/event_aliases.csv`.
4. Create or update the event watch for each source event.
5. Read `overrides/source_urls.csv` and create watches for its rows
   (World Dance Registry and long-tail events).
6. Event-site links: not a loop step. The planned `site` watch (every
   24 h from `upcoming` through `cooling`) is not built. The offline
   [event-site link report](../../journal/tools/collection/event_site_links.py)
   reads the sites of held WSDC events without results and drafts
   `source_urls.csv` rows for their WDR pages; a reviewer adds them
   ([D-0256](../../journal/decisions/0256-past-wsdc-events-reach-their-calendar-facts-and-wdr-pages.md)).

Discovery never deletes watches. Discovering a results link creates a pending
request; it does not establish that the sheet has been acquired or interpreted.
The event's [completion inventory](#event-completion) survives changes to its
parent's polling state.

## Work order

Each [loop](#loops) has its own timer and budget; no phase shares split one
run (the cycle and its allocator went in phase 5,
[D-0239](../../journal/decisions/0239-the-loops-are-the-only-orchestration.md)).
Release reconciles and builds supported corrections before a candidate. A
budget is a deadline, not permission to exceed a host limit or the
60-second write alarm. A zero budget still accepts inputs and starts no work.

Acquisition divides each host's existing request allowance between new,
current, identity, and old eligible demand. Initial percentages are:

| Host                | New | Current | Identity | Old |
| ------------------- | --: | ------: | -------: | --: |
| scoring.dance       |  50 |      25 |       10 |  15 |
| points.worldsdc.com |  10 |       0 |       70 |  20 |
| web.archive.org     |  50 |      10 |       20 |  20 |
| Other hosts         |  40 |      30 |       20 |  10 |

The picker uses actual issued requests divided by class weight. Empty
classes lend their share; host service rotates independently. A host
that is paused, over budget, or whose robots read failed (until its retry,
[D-0242](../../journal/decisions/0242-a-failed-robots-read-is-retried-on-the-host-backoff.md))
has no eligible demand, so its watches never take a pass's time. A class
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

A derive tick excludes an already attempted input fingerprint, rather than
the whole unit. Later parses can add inputs to a shared projection and let it
run again in that tick. Repeating the same bytes and recipe does not unlock
a blocked attempt on its own. Every failure gets a clock from the work queue
([D-0216](../../journal/decisions/0216-the-work-queue-leases-backs-off-and-quarantines.md);
[state: work queue](state.md#work-queue)). A derivation that raises retries
after 15 minutes, doubling per consecutive failure of one class up to six
hours, and each retry counts as a failure in the derive record; the third
quarantines the unit for seven days (the twentieth for `unit_io_error`), and
the watchdog's `[unit-quarantined]` names it. A blocked parse or a missing
artifact waits 24 hours doubling to seven days, then retries; a retry that
ends the same way counts in `waiting_repeats`, not as a failure, and not in
the per-source 10% rule. A deploy that changes the unit's input fingerprint
does not make the same error fresh: the same class and message is still a
repeat (D-0249). A unit another live owner leases is skipped
(`queue.lease_busy`). A unit takes no control lock (D-0238), so derive
reports no control timeouts. Derive wakes due queue rows once (`queue.woken`) and
names units it quarantined (`queue.quarantined`); a unit command, which holds
the state exclusive, releases every lease first. The derive record's
`derivation` key reports generations and equivalences
written, by `stage/kind`, and the replay epochs a cutoff moved.
`undeclared_reads` counts tables a unit body read with no rule, as
`stage/kind:table` ([read recorder](state.md#read-recorder)), and is absent when
there are none. If that read,
the replay-epoch read at the start of the tick, or the controls read fails,
the record keeps `derivation_error` or `controls_error` instead, and is still
written; without a starting read the replay bumps are left out. The watchdog's
`[report-failing]` names a record with either key or without `derivation`.
`current_hints_dropped` counts project and link hints offline selection found
current and deleted; a failed delete is noted as `current_hints_error`, does
not fail the tick, and no watchdog line reads it yet. A kind whose
recipe cannot be formed is skipped and named in `readiness_errors`, which
fails the tick.

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
Two actual requests per acquisition pass remain available for explicitly identified
identity dependencies. Ordinary unfetched rounds do not receive that
reserve. Robots, redirects, retries, and failed issued requests consume
it. One response can cross a threshold; these are admission high-water
marks, not hard limits on an unknown response size. Offline work continues.

`[scheduling]` in `config/sources.toml` configures the service gap,
high-water marks, repair request count, and watch recovery clocks; it refuses
unknown keys, the removed `reconciliation_share`, `acquisition_share` and
`offline_share` included. `build_interval_seconds` admits a release build at
most that often, failed or not; production sets 24 hours while the phase 1
switch drains, and unset means every release tick
([D-0199](../../journal/decisions/0199-contain-build-failures-and-pace-global-units.md)).
`history_interval_seconds` paced `project/history/all`, which is retired for
one `project/year/<yyyy>` unit per year (plan F17). Years are not paced; the
key is still accepted and paces nothing.
`release_min_free_bytes` (default 30 GB) is the free disk the release loop
needs before it builds.
`inventory_interval_seconds` (12 hours) paces `project/inventory/all`: it
waits that long after it last materialized
([D-0210](../../journal/decisions/0210-pace-the-inventory-like-history.md)).
A calendar replay the inventory has not read (its epoch is newer than the
highest one the inventory's current output recorded, over its generation and
every equivalence) lifts the wait, and so does a calendar generation newer
than the inventory's output (except `wsdc-history`, which writes no rows):
either rewrote live `events` columns the inventory enriched. Nothing else
does: the history lift and the starvation breaker left with history
([D-0222](../../journal/decisions/0222-derive-history-per-year-and-the-event-map-per-source-and-event.md)).
The units that read the inventory do not wait with it: while the gate holds
it, readiness takes it as settled at its pointer
(`derivations.settled_reason`), so after a dancer change a year, mapping or
resolution runs against the inventory's current output and runs again if the
inventory's next run moves it. Output alone decides a cutoff (plan F21,
[D-0224](../../journal/decisions/0224-releases-from-partition-artifacts.md)),
so an inventory re-run with unchanged output ends the cascade there, and a
changed one re-mints only the mappings, resolutions, events, years and links
whose own output changes. The
interval starts at the newer of the kept generation's time and its latest
cutoff (`derivations.materialized_at`). The rule lives in
`schedule/pacing.py`. Every loop tick names its scheduler there
(`loops/runtime`), so the phase-two
gate and `accept-year` treat a held inventory (`pacing.holds`: not current and
made within its interval), and the mappings and resolutions behind it, as not
pending and read the committed event list (I5). A current inventory holds
nothing, so a pending mapping or resolution behind it counts as pending. A tick
record's `phase_two_refusals` counts that gate's refusals by reason.
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

Five loops are the only orchestration, each a short timer tick that holds
`state.lock` shared and its own `loop-<name>.lock`
([D-0215](../../journal/decisions/0215-loops-share-the-state-under-a-reader-writer-lock.md),
[D-0239](../../journal/decisions/0239-the-loops-are-the-only-orchestration.md);
[state: locks](state.md#locks)). Derive runs the offline loop
(`schedule/offline.py`); plan and acquire run the event bookkeeping stages and
the acquisition pass (`schedule/acquisition.py`); release runs the build and
publish steps (`schedule/release_steps.py`).

Every tick (`loops/runtime.tick`):

1. Opens the state shared. A busy loop lock, a waiting or running writer, or a
   migration another process won skips the tick (`skipped-overlap` under
   `--timer`, exit 1 by hand).
2. Settles the active admissions of this loop's open runs: the loop lock
   proves them dead. Release also settles a stale publication as uncertain.
3. Skips when a closure-mode publication is in flight, or when one admitted
   since fences the run's start: its fence refuses every write, `runs` and
   `loop_state` included (F6). The record is only
   `runs/<run_id>-<loop>.json`, with `skipped: publication_in_flight`. A
   partitioned publication (`publication_narrow`) fences only what its check
   reads ([state: publication fences](state.md#publication-fences)):
   acquire, plan and derive tick through it, and maintain and release skip.
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
   and killed it at `TimeoutStopSec`. A tick writes the marker only for a
   SIGTERM within its budget or under `operator-hold`; one past its budget
   with no hold is its start timeout (every `TimeoutStartSec` is longer than
   the budget), so the overrun unit's death counts. A record file that says
   `process_interrupted` was written by a recovery that rolled back and is
   no ending. The other leases are released.
5. Captures the input bundle. Only derive accepts it (a changed bundle or
   journal that meets a narrow fence is left for the next tick,
   `inputs_deferred`); plan and release skip
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
   `skipped`, `duration_seconds`, `peak_rss_bytes` (the process's peak
   resident memory) and the write counters. Only acquire adds
   `control_timeouts`, the requests and fetches whose admission timed out on
   the control lock.
   The CLI exits 1 when `failed`.

| Loop       | Tick                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             | Progress                                                                           |
| ---------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------- |
| `acquire`  | Discovers configured and registry watches, then runs one acquisition pass to its deadline: due watches and the eligible `acquisition_plan` page rows, in plan order, compete under the fair policy; each history dispatch re-checks its target from facts before any mutation. A row whose page left the retained plan waits for the next plan tick (`stale_plan_rows`). Under backpressure no row is offered and the archive index is skipped. The event-timing observer wraps the pass but gets no archive offer proofs, since the rows were judged in an earlier tick. Flushes the pass's guards. It stops before the next request once a publication starts. A choice that raises stops only itself (`request_errors`): a busy or locked write, a write-deadline overrun, a publication fence, an operator pause or a control-lock timeout skips the request; any other exception holds its watch (guard `watch:<id> request_error`) for an hour, doubling, and a week from the third in a row, and a request that goes through clears it. The archive index and the guard flush still run. `failed` on a stage error, a request error other than a skip, or a `Blocked` host.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               | requests issued (fetches plus CDX pages)                                           |
| `plan`     | Runs the event bookkeeping stages (`[stages]` switches apply), the requirements scan and the registry verification summary. Then writes `acquisition_plan`: one row per `events.year` (`eligible`, `year_unaccepted`, `acceptance_digest_changed`, `year_findings_open` or `h7_h10_not_deployed`; `detail_json` holds the accepted digest and, as information only, `projection_pending`) and one per planned page (`eligible` or the candidate's stop reason, with `rank` in plan order and `via` archive). Rows are upserted in transactions of 5,000; year rows not written are deleted, and page rows not written only when the pass was not cut by its deadline (30 s are kept for the write).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              | plan rows written                                                                  |
| `derive`   | Wakes due queue rows, enforces the size cap, rebuilds parse hints (2 s), then runs the offline loop to its deadline. While release holds `heavy.lock` (any build while `[release] streamed_lock` is true, the default, or a correction) it starts no unit (`heavy_deferred`): a build and a parse tick together outgrew the 8 GB worker on the Mac (F10). Release-lag knob: while a build is due, release's last progress (or its last completed build attempt) is older than `release_lag_intervals` (2) × `build_interval_seconds`, and a release tick within the build interval was skipped `heavy_busy`, it runs only parse and link units, counts `release_lag` and keeps the guard `loop:derive release_lag` (1 h expiry); 0 turns it off. A release behind for another cause (not ticking, paused, `disk`, not building) is reported in `release_lag` with `engaged: false` and its `cause`, and derive runs as usual: holding project units would not help it. Readiness takes a prerequisite that is quarantined, waits for input, backs off, or that the pacing gate holds as settled at its pointer (`settled_prerequisites` names them). It stops before the next unit once a closure-mode publication starts; a unit write that meets a narrow fence is closed uncounted in the same tick (`queue.held.publication_fenced`). The registry crosscheck runs, with the heavy lock, when no parse or project unit waits. An exception that escapes a unit fails that unit (`unit_exception:<type>`), counted. `failed` on a unit failure, a stage error or a readiness error.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           | units whose output committed; other outcomes count under `not_completed` by reason |
| `release`  | Skips while `/run/swingset-disk` shows under `release_min_free_bytes` free (`disk`; guard `loop:release release_disk_low`, cleared by the next tick with room). With a hub (`--publish`), reconciles a pending publication, then builds a correction while work is queued and the baseline needs one ([publishing: corrections](publishing.md#corrections-and-rollback)); a dry run only reports `correction_needed`. A correction is admitted as `correction`, so it does not reset the build interval. Builds a candidate unless `build_interval_seconds` or the backlog rule defers it (`build_deferred`). A build and a correction hold `heavy.lock` exclusive, waiting up to 15 minutes, else skip (`heavy_busy`, guard `loop:release heavy_busy`, 3 h); meanwhile derive starts no unit. A streamed (partitioned) build takes it only while `[release] streamed_lock` is true (the default; the record says `heavy_lock`): the phase 3 rehearsal measured it at 6.7 GB before F26's fix, and the lock comes off once the worker measures it under 2 GB beside derive. A `/run/swingset-disk` reading older than an hour is unknown (`disk.stale`). The record carries `build_attempted` and `build_interval_seconds` for the watchdog, and a partitioned build's `partitions`: path, manifest digest and bytes, partitions built, withheld (by reason), rebuilt and reused, bytes written, seconds and peak memory ([D-0224](../../journal/decisions/0224-releases-from-partition-artifacts.md)). Every build, dry runs included, records its [gate verdict](publishing.md#publication-gate) as `verdict` (`correction_verdict` for a correction); a refused one publishes nothing, counts in `refused` and is not a failure ([D-0225](../../journal/decisions/0225-publish-only-strictly-better-releases.md)). Publishes with `--publish`. A `BuildError` or `ClosureError` is reported (`build_error`, `correction_error`) and fails the tick without raising ([D-0187](../../journal/decisions/0187-review-dismissals-year-scoped-aliases-and-non-fatal-builds.md), [D-0199](../../journal/decisions/0199-contain-build-failures-and-pace-global-units.md)). The build and publish steps are `schedule/release_steps` (`build_and_publish`, `correct`). | a build, a publication or a landed reconcile                                       |
| `maintain` | `wal_checkpoint(PASSIVE)` and the WAL size; deletes guard rows unseen for 7 days, idle loop records (no progress, not failed) and stop markers older than 14 days, and every loop record after 30 days; compacts the `summary_json` of loop `runs` rows older than 30 days to a stub (loop, failed, progress, finished_at), 5,000 a tick; deletes settled admissions older than 30 days but publications and the newest admitted and newest completed builds, 5,000 per transaction (`admissions_pruned`, D-0238), and reports the ledger's rows (`admissions_rows`, `[admissions-growth]` past 600,000); drops `work_queue` rows whose unit has nothing left to do ([state: work queue](state.md#work-queue)); counts building generations, those untouched for 24 hours, and partial generations no building row names (`incomplete_generations`; `holding` counts those that still hold references; an evicted generation is complete). Then it enforces the size cap and reports the controls and guard reasons, before any storage task. Then, each isolated (one that raises is `{failed, error}` and the next runs) and bounded by `[storage]` in `config/sources.toml`: removes partition artifacts and release manifests nothing has named for a day, unless a backup set is being written (`artifacts`, [state: partition artifacts](state.md#partition-artifacts)); removes a killed backup's scratch (`backup_scratch`); puts back lost objects from local backup sets and moves up to `evict_refs_per_tick` references of cold generations into packs (`evict`); packs up to `pack_rows_per_tick` old bodies (`pack_bodies`); every `collect_every_days`, runs the collector's full sweep (`collector`; [state: objects and packs](state.md#objects-and-packs)); runs the weekly verify sample ([operations](operations.md#verify-sample)); and, with 30 minutes of budget left, drills the newest backup set every `drill_every_days`, before verify once a day overdue (`drill`, [operations: restore drill](operations.md#restore-drill)). Each step checks the tick's stop.                                                                                                                                                                     | each task done                                                                     |

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
The mapping and resolution units remain the sole owners of canonical event
identity for listings.

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
after each turn and persist the position across ticks and restarts. Continuing
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

Each plan tick can refresh eight events, at most 32 distinct page requests per
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
A derive tick scans at most 100 candidates
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
watch's rows. Plan and acquire records carry `guards` (passes, rows
written and cleared) and `guard_reasons` (rows by reason). Rules and expiries:
[fetch layer, guards](fetching.md#guards).

### Stage switches

The top-level `[stages]` table of `config/sources.toml` turns optional event
bookkeeping off. Every key defaults to `true`. An unknown key or a value that is
not `true` or `false` stops config loading, so the removed `event_site_review`
key fails loudly. The plan loop runs these stages under the same switches.
The event-site review is no stage: it is an offline tool,
[`event_site_review.py`](../../journal/tools/collection/event_site_review.py)
(phase 5).

| Key                          | When `false`                                                                                                                                                    |
| ---------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `event_pressure`             | Plan skips pressure bootstrap and refresh. The gate opens: no `event_index` watch is denied, and starts, request debits and event turns write no pressure rows. |
| `event_progress_refresh`     | Plan skips the progress refresh.                                                                                                                                |
| `event_blocker_observations` | Plan skips blocker observations.                                                                                                                                |

A skipped stage starts no operation and leaves no key in the plan record. The
summary's `stages_disabled` lists the keys set to `false`. Pressure must open
its gate when off: nothing then refreshes observations or drains dirty rows, so
stale subjects or pending metadata would close expansion for good. Doctor and
the event report show the pressure gate as `disabled`. Stored pressure,
progress and blocker rows stay as they were. Two writers do not stop: the
migration 0019 triggers still mark a changed watch or snapshot dirty in
`event_pressure_dirty` (at most one row per watch; a re-bootstrap drains
them), and a fetched or parsed snapshot still appends an
`event_stage_operations` row, so that table keeps growing with snapshots.
Only pressure gates acquisition; the other two feed reports and
diagnostics.

Production sets all three keys to `false`
([D-0204](../../journal/decisions/0204-turn-off-the-event-bookkeeping-stages-and-open-the-pressure-gate.md)),
so no bookkeeping stage runs before acquisition and nothing they write gates
it. The code default stays `true`. The table takes effect with the phase 1
package; the 019 package ignores it, and all its stages run until then.
Turning `event_pressure` back on, by config or by rolling the package back to
019, needs no command. Plan's pressure bootstrap drains
`event_pressure_dirty` 100 watches a tick, and `metadata_pending` defers
new-event admission on every host until the dirty event watches drain. Each
drained result or event-index watch with a stored page, or a declared result
obligation, is enrolled as started, so every such page fetched while pressure
was off is counted. A request that stored no page while it was off is not,
until that watch is requested again; until then the gate undercounts those
starts and can admit a few too many events. On the 2026-09-25 16:10 UTC copy
about 1,900 watches got new snapshots per week, so expect about 5 hours of
closed expansion per week the stage was off. Hosts whose subjects went stale
can then stay latched high until the refresh, eight events a tick, catches
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
used. Retain blocker transitions and per-tick records rather than writing
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
projected dancer re-queues the links of every retained event with an entry
or judge its name can match, including old unresolved entries, through the
dancer unit's pool probe ([read sets](state.md#link-read-sets-plan-f20)).
