# Active plans

Plans describe remaining work and how to prove it is complete. They do not
establish that a feature is deployed. Start with [current status](../status.md)
for the latest retained operating evidence.

| Goal                                                          | Plan                                                              | Read first                                      |
| ------------------------------------------------------------- | ----------------------------------------------------------------- | ----------------------------------------------- |
| **Active, all other plans paused:** scale and tolerate faults | [Target architecture](architecture.md)                            | Invariants, then the phase plan                 |
| Run unattended without stalling                               | [Stability](stability.md)                                         | The rules, then the production sequence         |
| Recover identity output and missing event metadata            | [Data quality recovery](data-quality.md)                          | Measured regression and release checks          |
| Add historical results while improving recovery               | [History and recovery sequence](history-and-recovery.md)          | Stage order and gates                           |
| Preserve corrections and recover safely                       | [Recovery rollout](recovery/README.md)                            | Work packages and acceptance scenarios          |
| Bound operational storage without losing history              | [Bounded state and durable archive](bounded-state-and-archive.md) | Stages, gates, and known limits                 |
| Stop re-deriving output that has not changed                  | [Derivation invalidation](derivation-invalidation.md)             | Measured waste, then the ordered steps          |
| Fill historical gaps from event sites                         | [Historical source collection](historical-sources.md)             | Source restrictions and review requirements     |
| Move to PostgreSQL and Dokploy                                | [Migration](postgres-migration/README.md)                         | Approach, then the relevant implementation step |
| Admit score sheets with one contested row                     | [Row-level result holds](row-level-result-holds.md)               | Why, then the schedule                          |

The [project milestones](milestones.md) define dataset outcomes across these
plans. Earlier rollout documents live in the [archive](../../journal/archive/README.md).

## Reading older work IDs

- **V1–V7:** stages in the history and recovery plan.
- **H1–H18:** recovery and data-quality changes in the recovery rollout.
- **WP:** an implementation work package. Early packages are in the
  [first implementation plan](../../journal/archive/v1-implementation-plan.md);
  historical packages are in the [backfill reference](../reference/backfill.md#work-packages).
- **G1:** the controlled registry probe rehearsal.
- **M:** a milestone; the PostgreSQL plan has its own explicitly scoped M0–M9 steps.

Use descriptive titles in new writing and retain these IDs only to connect
existing plans and evidence. A new proposal belongs in a
[decision record](../../journal/decisions/README.md) when it changes a lasting choice.

## Paused plans

On 2026-09-25 the owner paused current work to implement the
[target architecture](architecture.md). The plans above stay as references
and resume when a phase of that plan says so. Operating the worker safely is
not paused.

## Maintenance queue

Maintenance gets one session a week ([D-0175](../../journal/decisions/0175-fewer-records-receipts-not-bundles-and-tiered-review.md)).
Queue side quests here instead of starting them; take the top item in the
weekly session.

1. [Bounded state](bounded-state-and-archive.md), remaining steps: parked until
   a measurement receipt shows the live database over 20 GB or the worker disk
   under 100 GB free. The one open item is the A0/A1 source JSON compression
   benchmark (attempt 003) and its decision, which the owner asked to finish.
2. [PostgreSQL and Dokploy migration](postgres-migration/README.md): a target,
   not started.
3. Step Right archive-only index bootstrap and `recognize` rules, then DCN's
   score PDFs (M4): the acquisition paths V5's new sources still lack
   ([plan note](history-and-recovery.md#event-completion-follow-up-in-v6)).
   DCN's results payload is built (D-0255, finals only); its PDFs add prelims,
   bibs and marks but print names the results tab masks, so they need a rule
   for masked dancers first ([guide](../reference/sources/danceconvention.md#13-open-items)).
   This is release work, queued here only until the parse backlog drains.
4. [Derivation invalidation](derivation-invalidation.md): measured and planned
   on 2026-09-23; steps 1, 2 and 5 are implemented offline as the target
   architecture's phase 1, not deployed, to ship in one switch. Until then one
   edited line changes every unit's recipe, so every deploy re-parses 32,522
   snapshots and re-derives the whole graph; 99.98% of the replacing
   generations produce byte-identical output. Batch every Python change and
   prefer none. Its step 3 collection of unnamed closure proofs shipped early
   in candidate 018 (D-0200); superseded row references and parse evidence
   remain. The plan's trigger is a full day of clean phase-two cycles. This
   replaces the former items 4 and 8, which are folded into it
   ([investigation](../../journal/investigations/2026/derivation-invalidation-2026-09-23.md)).
5. A dismissed `event_alias` finding should also settle the event's dates. Today
   the dismissal only hides the finding: the inventory projector skips the
   ambiguous listing, so the row keeps whatever an earlier pass wrote. After
   the 2026-09-21 review, `2025-01-countdown-swing-boston` still carries the
   stale "(Unconfirmed)" span of Dec 26 to 31 2024, and
   `2016-09-sunny-side-dance-camp` stays month-only although the reviewer's
   note names Sep 4 to 11 2016. Let a dismissal carry the chosen span, or
   fall through to the widest current listing
   ([projector](../../src/swingset/project/history.py), D-0187). Release work
   for the date item in the [data quality plan](data-quality.md).
6. Split the checkpoint transport archive across files. The remote backup
   refuses an archive over 20 GiB, and the live database passed 24 GB on
   2026-09-22, so remote checkpoints fail until the archive shrinks; the local
   copy is the only backup meanwhile ([D-0192](../../journal/decisions/0192-a-failed-backup-waits-for-the-next-timer-run.md)).
   In the same change: check the archive size before copying anything,
   reuse a local checkpoint younger than an hour instead of copying again,
   and link the content-addressed blobs into a new checkpoint instead of
   rewriting them, since only the database changes between copies. Touches
   backup and restore, so it takes the full suite.
7. Decode zstd replays from the Wayback Machine. Two 2026 captures of
   `worldsdc.com/events/` were archived with `content-encoding: zstd`; the
   replay path stored the still-encoded bytes as the body and the parser saw
   no rows. Fixed and deployed 2026-10-12 05:52 UTC (tree c040): the client decodes
   zstd with pyarrow inside the same byte limits and refuses any other
   encoding httpx leaves undecoded
   ([limits](../../src/swingset/fetch/limits.py)), and the calendar extractor
   decodes the two stored bodies (169 and 174 rows). The deploy re-parses the
   wsdc_calendar, swingdancecouncil and wsdc_newsletter kinds (about 210
   snapshots), since the council and newsletter adapters import the calendar
   adapter. Both captures parsed at 07:28 UTC with 174 and 169 observations
   ([receipt](../../journal/evidence/runtime/parse-failures-2026-10-12/receipt.json)).
   Left: drop the two captures' four dismissal rows from
   `finding_reviews.csv` (2026-09-22) in a batched override change.
8. Human identity review website (H17) and the JesAnn history page as a site:
   deferred, see [D-0055](../../journal/decisions/0055-defer-human-adjudication-to-a-review-website.md).
9. Bring the core test run back under three minutes. On 2026-09-26 it took
   194 s for 1,264 tests alone on the Mac (228 s in the phase 1 review; about
   203 s at phase 0). Most of the rise is new tests in existing core files.
   Profile with `--durations=30` and move exhaustive parameters to extended,
   keeping each behavior the [testing guide](../guides/testing.md#what-the-core-protects)
   lists in core; the 33-page cohort alone takes about 20 s. After phase 1's
   last review round: 1,277 tests in 193 s. No phase 1 test is among the 40
   slowest (each under 0.5 s), so moving them would not help; the 22
   `test_restart_at_pipeline_checkpoint` cases take about 2 s each (45 s).
   Accepted as debt for phase 1's switch (at close, 2026-09-27: 1,304 tests in 196 to 255 s);
   both slow groups are behaviours the testing guide keeps in core, so the
   fix is faster fixtures, not a smaller selection. After phase 2
   (2026-09-27): 1,492 tests in 245 s. Its seven new core files add 122
   cases and about 31 s; 21 s of it is `test_loop_isolation.py` and
   `test_loops.py`, which run whole loop ticks, some in subprocesses.
   After phase 2's second review fixes (2026-09-28): 1,537 tests in 284 s
   on the Mac; the busy-writer publication test no longer waits out 5 s.
   At phase 2's close (2026-09-29): 1,537 tests in 252 s.
   At phase 3's offline close (2026-09-29): 1,664 tests in 344 s. Two
   tests take 41 s of it: the armed read-set run of the pipeline fixtures
   (`test_read_sets.py`, 22 s), which the core keeps as the recorder's
   acceptance, and the 33-page cohort (20 s).
   At phase 3's close (2026-10-02): 1,736 tests in 340 s, after the fix
   rounds moved fast regression tests into core; still about twice the bar.
   At phase 4's close (2026-10-03): 1,938 tests in 440 s (795 to 1,055 s
   on a loaded Mac); phase 4 added `test_storage`, `test_body_packs`,
   `test_output_packs`, `test_collector`, `test_storage_migration` and
   `tests/backup/*` to core.
   At phase 5a's offline implementation (2026-10-03, before its rehearsal):
   1,954 tests in 466 s; 5a added `test_phase5_migration` and
   `test_origin_candidates_tool` to core, and `test_event_site_review_tool`'s
   write-nothing and unaccepted-year cases (the rest of the file is
   extended). At phase 5's offline close (2026-10-03, after 5b): 1,945
   tests in 489 s; removing the cycle did not lower it. The `event_*`
   removal (open decision 2) deletes six whole core files. After phase 5's
   review fixes (2026-10-04): 1,951 tests in 518 s, beside a rehearsal.
   At phase 5's close on the final tree (2026-10-04): 1,955 tests in 524 s,
   beside a rehearsal.
10. Bound the memory of the publication report's closure check. It holds a
    whole closure (I10): `swingset summary` peaked at 4.5 GB on the 35 GB
    copy, 4.4 GB of it this check, under the unit's 5 GB cap, and the file
    grows about 5 GB a day. Stream the closure instead of holding it, and
    record the worker's `MemoryPeak` for `swingset-summary.service` first
    (the phase 1 post-check has it). `[summary-failed]` fires if the cap is
    hit before then.
11. Fix the nondeterministic `project/source_event` output that phase 1's
    cutoff flagged (`nondeterministic_output`, five cycles on 2026-09-29): the
    same inputs produced different rows, so find the unordered iteration or
    clock read and pin it with a test ([receipt](../../journal/evidence/runtime/architecture-phase1-2026-09-29/receipt.json)).
12. Move the worker to an always-on host. Production is a VM on a laptop: on
    2026-09-30 the Mac slept on battery from about 21:00 to 18:00 UTC, so
    cycles, timers and a workflow agent stalled for 21 hours. Until the move,
    keep the Mac on power with the lid open, or run `sudo pmset -a disablesleep 1`: `caffeinate` does not stop clamshell sleep on battery, and on 2026-10-02 the lid closed at 19:00 UTC and the worker froze for 8.7 hours. Make
    host independence a trigger for the [PostgreSQL and Dokploy](postgres-migration/README.md)
    move (phase 6) rather than waiting for data size; it also gives off-machine
    backups.
13. Stop backpressure counting superseded parse rows. They keep their
    `pending_work` row and get no `work_queue` row, and `schedule/fairness.py`
    counts a missing queue row as runnable, so the thresholds stay raised
    (D-0230). Exclude them, or clear them, then return `pending_parse_items`
    and `pending_parse_bytes` to their defaults.
14. Phase 4's carried-forward minors: read races in the accessor, the
    retry file's `xb` open, body packing's stall and catch, the drill's place
    in the budget, sets that list `missing`, lost-object scans that grow, two
    noisy alerts, and the surviving mutants. The list and its order are in
    the [architecture plan](architecture.md#9-migration-plan), phase 4
    ("Carried forward"). Take them after phase 4's deploy, in that order.
15. Give `scheduler_requests` a collector. Nothing deletes its rows, and the
    admission prune keeps every request row it names (14,546 of 14,570 on
    the 09-29 copy), so the ledger grows by about 1,800 rows a day and each
    prune batch scans the kept rows (D-0238). Delete rows older than the
    quota window the scheduler reads, then their admissions; the maintain
    record's `admissions_rows` shows the growth until then. Trigger: the
    watchdog's `[admissions-growth]`, maintain's `admissions_rows` over
    200,000 (about 100 days of growth from the 09-29 copy). This is a
    collector, not the removal open decision 2 is about.
16. Phase 5's carried-forward minors: drain liveness from a lease that is
    never renewed, errors after a pause commits, the orphan sweep's read
    race, CDX admissions without a run, cycle-run admissions, publication
    uploads in `[admission-stuck]`, a re-created requirement's opening
    time, the site-review tool's immutable open, and the card's
    site-review gap count. The list is in the
    [architecture plan](architecture.md#9-migration-plan), phase 5
    ("Carried forward"). Take them after phase 5's deploy, in that order.
17. Stop the 11 known parse failures from failing derive ticks (investigated
    2026-10-12). They never quarantine: `parse_failed` and `unsupported_page_kind`
    wait 24 h to 7 days, but a deploy that changes a unit's input fingerprint
    makes the retry count as fresh, which fails small ticks after deploys (12 of
    13 failed derive ticks since 10-04). Groups: scoring.dance event 179 (5,
    source-side, empty competition list), 2016 Wayback events-map captures (4,
    undated markers plus a commented example marker the extractor counts), the
    2026 `/events/` captures stored zstd-encoded (2, item 7; 343 recoverable
    rows), and three registry-dump snapshots still queued for parse. Deployed
    2026-10-12 05:52 UTC: zstd decoding (item 7), and a waiting
    failure with the same class and message is a repeat even under a new
    fingerprint, with the error kept in the attempt evidence
    ([D-0249](../../journal/decisions/0249-a-waiting-failure-with-the-same-message-is-a-repeat.md)).
    Each old unit counts as fresh once more, since its attempts kept no message
    (one failed tick at 05:57). Event 179's watch was retired by a one-row
    write (owner: keep it blocked, stop re-fetching). A waiting row wakes only
    on its clock, not when its inputs change, so the two zstd units needed an
    operator requeue. Left: a `swingset watch retire` command if this recurs,
    an optional extractor for the 2016 events-map captures, and stopping
    registry-dump parse work.
18. Deduplicate the mm2025 eepro index, which lists every file under two letter
    cases (`Strictlies.html` and `strictlies.html`); six pairs are both accepted
    with identical content, so contests may count twice. Then admit the two
    parked eepro strictly rounds (one has an empty trailing cell). From the
    2026-10-06 admission adjudication; the owner kept 23 rounds whose
    disqualification note contradicts the shown result blocked.
19. Admission adjudication follow-ups: the projector drops final marks such as
    `4|SVW` and `8 SV2`; published callback sums leave out chief-judge
    adjustments; Pro-Am Follower rounds label both partners followers; check
    other adapters for the dict-to-text key-order bug found in WDR rounds.
20. Narrow the code scopes of admission contracts. Any edit to
    `admission/contracts.py` or `round_contract.py` changed 41 of 45 recipe
    scopes and re-ran every parse, project and link unit: contract 5 queued
    37,669 parse (5,908 with changed output), 33,394 project and 2,934 link
    units for two page kinds, and tripped backpressure for an hour (D-0243).
    Built 2026-10-15 as D-0261, reviewed 2026-10-15 (approve with conditions,
    met): adapters no longer import
    admission (`contracts.declare` attaches the declaration in
    `schedule.parse`), each kind's rules and version live in the module
    `admission/kinds.py` names for it, `evaluate` moved into the parse-only
    core, and scopes hold only the code digest of the registry and the kind
    table, so a lookup edit re-runs every scope that reaches them. Tests in `test_recipe_scoping.py` pin it: a kind's contract edit
    changes only its kinds' parse scopes (the calendar's: 144 snapshots
    today), a core edit only the 20 parse scopes, and a registration only its
    own kinds' scopes. 1,459 sampled production snapshots and 31 fixtures
    give identical extracts, results, declarations and reports. Left:
    `admission/report.py` is still in all 51 scopes and
    `sources/interpretation.py` in 47; `generations.py` and `support.py` reach project and link through
    `state.parse_recovery` and `derivation_dependencies`. The combined deploy
    re-runs 39,909 parse, 33,550 project and 2,936 link units (production
    counts of 2026-10-15), the same as the changes it ships with alone.
    Next: the owner chose on 2026-10-15 to ship it in one deploy with the
    finished but undeployed D-0255 (danceconvention.net), D-0256 (calendar
    adoption and the intake fix), D-0258 (SwingDirector PDFs) and D-0257
    (the calendar contract, in shadow), which also change every scope.
    D-0257's writer fix must be deployed before the calendar watch reopens:
    without it the first live capture deletes the 2026-08-13 Wayback rows. That deploy adds the three approved override rows
    (Charlotte WestieFest's WDR link in `source_urls.csv`; `ATX ROX 2026 - West
Coast Swing Magic` to `wsdc-300` and `KOREA WESTIVAL 2026_10th Anniversary`
    to `wsdc-268` in `series_aliases.csv`; exact text in
    [the investigation](../../journal/investigations/2026/missing-2026-results-2026-10-15.md))
    and then reopens the sealed WSDC calendar watch `c6f6cc3dde20493f` with
    receipts. The same deploy adds the 42 SwingDirector PDF rows to
    `source_urls.csv`, reviewed and approved by the owner on 2026-10-15
    ([rows](../../journal/investigations/2026/swingdirector-source-urls-2026.csv));
    they stay inert until the source is enabled. Do not edit `overrides/`
    before then: production reads it live.
21. Re-admit the 309 scoring.dance event pages whose stored observations carry
    older labels than their accepted generation, so releases can use them; the
    contract 5 re-parse repeats their generation and does not rewrite them
    (D-0241). Built 2026-10-14 as `swingset relabel` (operator command, in no
    recipe scope): it found 479 such units (309 `scoringdance.event`, 103
    `eepro.round`, 67 `eepro.autoindex`) and rewrote them in 50 seconds on a
    copy. Production runs it once with the D-0253 deploy
    ([operation](../guides/operation.md#reparse-and-registry-work)).
22. Make round contract 5's `source_remark_outcome_conflict` guard decide
    "placed or called back" the way the projector does: it reads only
    `row-data-state` and a column headed `placement`, while the projector
    places every final row and also reads `place`, `result`, `final` and
    promote/alt columns (`project/contests.py`). A future page layout could
    otherwise admit a disqualification remark as eliminated. No stored page
    does today (independent check of 2026-10-07).
23. A robots.txt that answers 200 but disallows the whole site still takes
    the per-watch skip, so its due watches could starve other hosts as the
    failed reads did on 2026-10-08 and 09 (D-0242 covers failed reads only).
    Push a disallowed watch's next check forward, or skip a fully disallowed
    host once per selection.
24. Choose a kept generation's read set by input fingerprint, not by recency.
    `build/partitions._read_set`, `derivation_dependencies._manifest_reads` and
    retention's live rule take the newest equivalence; if a unit's inputs go
    F0 -> F1 -> F0, the generation is reused without a new equivalence and the
    newest names F1's set. Prefer the row whose fingerprint equals
    `derivation_scopes.desired_fingerprint`, falling back to the newest, in all
    three together. Production had no such case on 2026-10-11.
25. Fix the scoring.dance parser's role flip on follower-first finals. In 34
    contests across 27 events (for example 5280 Westival 2024 Sophisticated
    J&J) it reads a final that lists the follower first as leaders, so finalists
    get the wrong role and a second entry. D-0248 withholds their printed IDs
    as `role_contradicted`; the fix returns them. Found by the H17 review of
    2026-10-11.
26. Record why a fetch failed. A request that fails is classified `ServerError` and
    retried, but neither the run summary nor the journal keeps the exception
    text, so on 2026-10-12 a home-network outage looked like a phase 5 defect
    until hours of checks ruled it out. Log the exception class and message
    per failed attempt, bounded, and count them by class in the acquire summary.
27. Stop a dancer re-run from holding every event for hours. `inventory` waits
    for every dancer to be current (`derivations._ready`), and mapping,
    resolution, event, source_event, year and new links all wait on inventory.
    After the H17 deploy on 2026-10-12, about 20,000 dancers re-ran at about 400
    per tick, so no event or year moved from 04:07 UTC for over four hours and
    `[stale-progress]` fired at 06:05. Narrow inventory's wait to the dancers it
    reads, or let it use the dancers' previous generations while they re-run.
28. Land the gate's per-event result check (D-0251). Refuse a candidate in
    which an event with placements or entries in the baseline has none
    (`lost_result_events`), approved by name with a `result_event:<event_id>`
    row in `release_decreases.csv`. Built 2026-10-14 to ship with D-0253,
    which moves the same scopes: an event whose contests all moved to
    another event is `moved`, not lost (D-0254). Left: move the gate out of
    the project and link scopes, so the next gate edit re-runs nothing.
29. Fix the regressions the owner accepted with release `3fe7bae` (D-0252):
    four September 2026 events (Bavarian Open, FinnFest, Retaliation,
    Swingtime Denver) lost city, country and region when a scoring.dance or
    EEPro header replaced the calendar row; nine events show a results URL as
    their website. Keep the calendar's place and website when another header
    wins.
30. Fix the watchdog's `[state-size]` measure. On 2026-10-13 it reported 61.9 GB
    against the 50 GB cap while the database was 41 GB: it counts `candidates/`
    (9.9 GB, about 100 kept candidates) and `operations/` (3.1 GB). Measure
    the database against the cap and give candidates a retention rule that
    keeps every published candidate and the `--revert-to` targets.
31. Attach result-site events to their WSDC event. In release `3fe7bae`, at
    least 14 2026 WSDC events (Capital Swing, MADjam, Boston Tea Party, USA
    Grand Nationals, Liberty, Orange Blossom, NeverlandSwing, German Open,
    Swingtacular, Bavarian Open, SwingTime Denver, River Swing Nights and
    others) hold their registry points on the calendar event while their
    eepro or scoring.dance results sit on a second event (`2026-06-liberty-swing`
    beside `2026-06-liberty-swing-dance-championships`). About 77 more 2026
    events with results have `wsdc_status = unknown`, many of them WSDC events
    (The Chicago Classic, Florida Dance Magic, Swingover, Derby City Swing).
    So only 12 of 63 past 2026 WSDC events show results on the WSDC event,
    though eepro alone is fully collected (34 of 34 events on
    `eepro.com/results/2026/`, checked 2026-10-13). Match source events to
    calendar and registry events by date overlap and name, as
    `docs/reference/sources/eepro.md` section 4 says, and add aliases where
    names differ (GNDC). This is release work: it moves results onto WSDC
    events and their registry points onto the same rows.
    Built in the working copy as D-0253 (2026-10-13) and rehearsed on a copy
    ([investigation](../../journal/investigations/2026/event-matching-2026-10-13.md)):
    state is right for all years, but 47 target WSDC events had withheld
    headers. D-0254, item 21's `relabel` and item 28 (2026-10-14) clear them:
    re-rehearsed on a fresh copy, 92 of 93 targets publish with results, the
    gate passes with no decrease and no lost result event, and 80 placeholders
    are `moved`. Deployed 2026-10-14 and published 2026-10-15 as `28f8196`
    after two independent checks: events 2,114 to 2,191, events with results
    276 to 399, 80 moved and none lost
    ([receipt](../../journal/evidence/runtime/event-matching-2026-10-14/receipt.json)).
    Done. Budafest 2026 stays withheld: one scoring.dance round page has no
    accepted generation.
32. Let the inventory's listing upsert notice a new `parser_version`
    ([projector](../../src/swingset/project/history.py)): its `WHERE` clause
    compares the snapshot id but not the parser label, so after `relabel` 2026-10-14
    the `events` rows of relabelled scoring.dance listings still cite parser 3
    and the release publishes them through D-0254's listing header or the
    baseline's row instead. A project-scope change: ship it with the next one.
33. Fix four findings the gate does not measure, from the check of release
    `28f8196` (2026-10-15): `2026-04-city-of-angels-wcs` is dated 2027-04-29
    because scoring.dance listing 315 prints 2027, so its rows land in 2027
    files; `2026-11-the-open-swing-dance-championships`, a calendar event, went
    from `registry` to `unknown`; eight EEPro source-event coverage rows lost
    their page counts (`event_evidence_byte_budget`); and
    `2026-09-global-grand-prix-wcs` (`wsdc-342`) still publishes empty beside
    the `wsdc-409` event.
34. Read `J&J` and `JnJ` as Jack and Jill in `normalize/divisions`. It matches
    only "jack" and "jill", so DCN contests such as `Novice J&J` and
    `JnJ novice` (seven of the 14 DCN events of 2026) project as contest type
    `other` and lose points eligibility; other sources' contests with those
    names change too ([investigation](../../journal/investigations/2026/dcn-results-source-2026-10-15.md#gaps-left)).
35. Activate the WSDC calendar contract (D-0257, release work). It dates 42
    published events, the 21 registry-only 2026 events among them, and adds
    341, 29 of them held registry occurrences ([measurement](../../journal/investigations/2026/calendar-contract-2026-10-15.md)).
    First, in `project/history.py`: a listing whose name gains a note
    (`Bavarian Open (Cancelled due to Covid-19)`) must update its edition's
    row, not add a twin (153 twins would publish); and the history row
    should carry the listing's city, region and country (five September
    2026 events would lose theirs; 304 events would gain one). Built in the
    working copy as [D-0260](../../journal/decisions/0260-an-unreported-edition-is-one-row-per-series-and-month.md)
    (2026-10-15) and simulated read-only with the contract active: twin
    groups 165 to 1 (Korea Westival 2020, which one capture dates twice),
    184 twin rows removed, events 2,191 to 2,351, cities 165 to 391. No
    published event loses dates or place except the stale `wsdc-342` Global
    Grand Prix 2026 row, whose place is on the held `wsdc-409` row; Monterey
    Swingfest 2021, published twice, becomes one event. Amended 2026-10-16
    after the blocked candidate `cand_ce7e0a45f402400a`: until activation a
    cancel-marked listing keeps its own row (`KEEP_CANCELLED_TWINS` in
    `project/history.py`); turn it off in the activation deploy so the twins
    merge. Then record
    an independent review of a contract-1 cohort and activate it with
    `swingset contract activate`, with preflight and post-check receipts.
36. Declare the inventory's new reads. After the 2026-10-15 deploy the watchdog
    reported `[read-set-widened]` for `project/inventory`: undeclared reads of
    `rounds`, `callbacks`, `callback_marks`, `final_marks` and `heats`, most
    likely the D-0260 twin-deletion guard in `project/history.py`. A widened
    read set re-runs inventory links on any change of those tables. Declare
    the reads in `state/read_sets.py`, or narrow the guard's query.
37. Let acquire skip a tick when the write lock is busy instead of failing.
    During the 2026-10-16 link drain, acquire failed three times in 15
    minutes with `database is locked` at `BEGIN IMMEDIATE` in
    `schedule/discover.py` (about 15 s of lock wait while link writes ran up to
    14 s). Nothing was lost, since backpressure held requests anyway, but each
    failure fires `OnFailure`. Treat a lock timeout before any request as a
    skip, as derive does with a busy lease.
