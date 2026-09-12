# Does Swingset repair missing data automatically?

Partially. Swingset has durable retries and propagates changed evidence through
parsing, projection, identity linking, and publication. It does not yet have a
complete feedback loop from **missing data → repair action → verified recovery**.
Several gaps can persist while scheduled runs succeed and the work queue is empty.

This diagnosis uses the [September 12 missing-data audit](missing-data-2026-09-12.md),
its SQLite capture at **16:37 UTC**, and the runtime source in this checkout.
The captured publication is `845d92a04e30b03d4a7081655e754ec2cea323aa`;
the deployed runtime revision is `064066d0ec64516ac96b2c3954971050df8219f3`.
Subsequent local commits through `a90eaac8` change design and formatting tooling,
not these runtime paths. The diagnosis used three Sol subagents, code inspection,
queries against the captured database, and offline reproductions. No live source
requests, production changes, or repairs were made.

## Which gaps will recover by themselves?

| Gap at capture                                                | Cause                                                                                        | Automatic recovery                                                                                                                                           |
| ------------------------------------------------------------- | -------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| 4,482 unfetched scoring.dance rounds                          | Known URLs exceed the daily request allocation.                                              | Expected under continued successful operation. Five full 800-request allocations plus 482 requests on a sixth budget day, before other traffic and overhead. |
| 64 unmapped scoring.dance events                              | Accepted metadata lacks dates; one event page fails parsing.                                 | Only if supported dated evidence arrives. Stable old pages need parser or reviewed mapping work.                                                             |
| No canonical events for 2010–2017                             | Historical enumeration/adapters remain draft work.                                           | No. Repeating enabled discovery cannot reach inputs it does not know how to enumerate.                                                                       |
| 138,280 post-2010 WCS registry placements without event joins | Reconciliation only matches existing canonical events by normalized name and month.          | Conditional on a unique matching event appearing. Indexes can add events independently, but no acquisition path targets these placement gaps.                |
| All 3,791 judge IDs null                                      | Current automatic candidates cannot reach the publication confidence threshold.              | No, under the current scoring model and evidence.                                                                                                            |
| Some unrestricted-division and mixed-person identity errors   | Incorrect interpretation of division and source names.                                       | No. Repeating the same semantics reproduces the error; more candidates can worsen mixed-person matches.                                                      |
| 136 reference dancer IDs absent locally                       | Current registry responses explicitly say not found.                                         | Annual rechecks can recover IDs if the source restores them. Re-fetching the same response cannot recover historical claims.                                 |
| 26 source-asserted entry IDs without dancer rows              | Source IDs bypass registry verification; three distinct IDs were never requested.            | No general targeted lookup exists. Ten distinct IDs have annual not-found rechecks; three have no watch.                                                     |
| 303 conflicting registry placement keys                       | Current evidence disagrees, so projection withholds the ambiguous values.                    | Only changed evidence or a reviewed semantic repair can resolve the disagreement.                                                                            |
| Sparse marks, missing names, heats, and metadata              | A mixture of unavailable source fields, unsupported semantics, and unimplemented collection. | Depends on the field and source. A null does not itself schedule evidence collection.                                                                        |

Counts are not additive. For example, missing event coverage causes many missing
registry joins. An ended calendar event without results is not proof that a public
competition result exists. Couple entries do not need an individual WSDC ID, and
first-time finalists can legitimately lack one until the registry issues it.

## 1. Freshness and semantic change are being confused

Two registry defects share a root cause: **a successful check can leave the data
unchanged, but its freshness still needs to advance**.

### New-number probes can reject their own fresh results

The fetcher deliberately archives and reparses repeated registry probe responses
to establish fresh evidence. However,
[`store_parse_result`](../src/swingset/state/observations.py#L84) retains the old
observation row when its payload is identical. It advances the watch's successful
snapshot pointer, but not the observation's snapshot reference.

[`_lookup_outcome`](../src/swingset/schedule/registry.py#L128) checks freshness using
the observation's snapshot timestamp. A valid new `not_found` response can
therefore fail the probe's freshness boundary. The cursor does not advance.
Discovery sees that the watch was already checked after the probe started and
does not force another request; ordinary registry scheduling is annual.

The offline reproduction fetched and parsed the same verified not-found response
on September 1 and September 8. The successful watch pointer advanced to September
8, but the observation retained September 1. The probe stayed at the same ID.
This is a **confirmed code defect**, not proof of an already-stalled live probe:
the pinned capture had zero diverged registry snapshot pointers, and its first
post-sweep probe was waiting for request budget.

This threatens the intended daily probes for recent Newcomer/Novice finalists
and the weekly probes for numbers issued throughout the year. Finishing the
initial numeric sweep does not prove ongoing new-number discovery works.

### Annual profile refresh can keep selecting the same dancers

The stale-profile trickle selects the oldest 100 dancers by
[`dancers.registry_fetched_at`](../src/swingset/schedule/registry.py#L53).
An unchanged successful fetch normally creates no new snapshot or projection, so
that timestamp stays old. The same unchanged dancers can be made due the next day
and occupy the same 100 slots indefinitely, starving later dancers of this
accelerated refresh path. Independently scheduled annual watches may still run.

The offline fetch/parse/project/discover reproduction confirmed an unchanged
profile remained stale and was selected again the next day. This is prospective
refresh behavior; the recent initial sweep has not yet aged a year.

Repair both by separating the timestamp of successfully verified content from
the timestamp of its last semantic change. Probe advancement should consult the
latest successful parse; stale-profile selection should consult successful check
freshness. Preserve the original evidence provenance and avoid relinking every
event merely because an unchanged page was checked again.

## 2. Identity processing cannot repair some of its own interpretations

Judges are built with unknown role, no division, and no source WSDC ID.
Their eligible registry pool contains 1,292 dancers, all with leader or follower
primary roles. The [score](../src/swingset/link/score.py#L22) removes unavailable
division weight but retains role weight even though the judge's role is unknown.

With a perfect name match and recent activity, the maximum is:

```text
(0.65 name + 0 role + 0.05 recency) / (0.65 + 0.10 + 0.05) = 0.875
```

The [probable threshold](../src/swingset/link/service.py#L344) is 0.90. Judges also
cannot use the entry-only registry-placement confirmation path. In the capture,
the maximum observed judge confidence is exactly 0.875: 2,562 possible and 1,229
unmatched. This fully explains why all 3,791 published judge IDs are null.

The remedy needs judge-specific treatment of unavailable evidence and validation
against known identities. Lowering a global threshold would affect dancer links
and is not justified by this finding.

Existing issues #16 and #17 are another semantic failure class.
[`_division_ok`](../src/swingset/link/candidates.py#L64) treats `none` as a ranked
level, which can reject dancers in unrestricted contests. Token-set candidate
matching can nominate one dancer contained inside a two-person source string.
The audit's 16,552 unrestricted individual-role entries and 4,061 lexical
mixed-name entries are affected populations to investigate, not counts of proven
wrong links. More fetching cannot correct these interpretations.

Unique embedded source IDs also publish directly as `confirmed/source_id`
without requiring a dancer row. The three unrequested IDs—64,292, 94,704, and
236,870—are not reached by normal frontier probing. Recent eligible finals have
a targeted confirmation path, but there is no general “source ID has no dancer”
lookup path. These IDs need bounded direct verification, not a blind sweep to
236,870 or automatic acceptance as valid registry identities.

## 3. Scheduling bounds load but does not guarantee progress for every class

The current scoring.dance backlog is recoverable. New child watches start live,
priority zero, and due immediately. Policy runs after the first non-skipped fetch
response and moves known historical events to archived priority, normally with a
90-day interval; errors can shorten the retry interval. In the pinned
ordering, the first 800 scoring.dance watches at the next daily budget boundary
were all never-fetched rounds. Budget refusals preserve due state for later runs.

However, [`due_watches`](../src/swingset/schedule/watches.py#L130) uses strict
priority and due-time ordering without age promotion or reserved acquisition
capacity. The 64 date-less event watches cannot age out of live polling because
they lack mapped event dates. At approximately hourly polling they can demand
roughly 1,500 requests per day, above the host's 800-request cap. After the current
backlog drains, these watches risk consuming the budget ahead of indexes and
archived refreshes. This is a capacity risk inferred from policy, not a measured
future starvation incident.

Mapping and scheduling also interact late: event-date policy is applied after a
fetch. New child watches receive no initial date classification. Correcting the
mapping eventually corrects polling, but unresolved metadata can cause repeated
low-value requests in the meantime.

Some watches can stop permanently: repeated gone responses, or expected
unavailability over 30 days, lead to `state='gone'`. Gone watches are excluded from
due queries; idempotent discovery does not revive them. None existed in the
capture, so this is a future late-publication limitation, not a cause of the
current counts. An occasional bounded recheck or new parent evidence could be a
revival trigger.

## 4. Missing evidence has no general acquisition path

The runtime enables five adapters: calendar, dancer registry, EEPro,
scoring.dance, and WDR. WDR relies on explicit event URLs. Research CSVs are leads,
not scheduler inputs. DanceConvention, other result platforms, and the proposed
Wayback/history machinery are not implemented runtime discovery.

Registry [event reconciliation](../src/swingset/project/registry_events.py#L24)
only links to existing canonical events. It does not create historical events,
discover aliases, or investigate month offsets. Zero matching events produce no
finding; multiple matches do. Thus the largest registry join gap is mostly silent
from a repair-planning perspective.

The 64 scoring.dance events likewise need dated evidence before mapping. The
event parser recognizes a particular date layout. Sixty-three have successfully
parsed event observations without dates; event 179, Hippmann Fun-Competition 2024,
has the current parser failure. Repeating unchanged pages cannot supply a date
the parser never extracts.

Reference mismatches are a different limit. The 136 missing dancer IDs have
verified current not-found responses, and conflicting registry claims are
deliberately withheld. The pipeline should retain that uncertainty. It needs a
separate historical-evidence policy to use the comparison archive; it cannot
silently treat old claims as current source truth.

## 5. Durable retries do not cover every failure

The foundations are sound:

- [Work completion](../src/swingset/state/work.py#L89) commits output, downstream
  work, and removal of the work unit together. A crash does not lose half a unit.
- Host cooldowns and daily budget accounting survive process restarts. The normal
  timer retries on later cycles.
- Registry parse failures clear cached validators and schedule bounded retries.
- Changed observations enqueue projection; changed dancers relink events.
- Explicit parser, extractor, projector, linker, and override version changes
  enqueue the appropriate replay work.
- Candidate construction and publication use durable intent, atomic filesystem
  promotion, and remote receipt reconciliation to recover interrupted publishing.

There are two material limits:

**Handled non-registry parse failures leave the work queue.** The parser records
a failure finding and preserves earlier good observations. With the extractor
version already cached as current, a later identical 200 or 304 normally queues
no new parse. The same failed bytes therefore remain
unprocessed until the source changes or a versioned fix triggers replay. For a
deterministically unsupported layout, repeating the same parser would not help;
the missing mechanism is bounded retry where appropriate and explicit repair
tracking. A source-code edit alone does not replay data: the relevant explicit
version must change. See [parse handling](../src/swingset/schedule/parse.py#L118)
and [invalidation](../src/swingset/state/work.py#L113).

**Unhandled artifact failures can block all ingestion.** Missing/corrupt archive
bodies or extracts can raise exceptions outside that handled failure path. The
work remains pending, but the next run retries the same broken artifact. Since
[`run_cycle`](../src/swingset/schedule/cycle.py#L151) suppresses discovery and
fetching whenever work is pending, a single unrecoverable unit can repeatedly
block new acquisition. There is no general artifact restore/refetch planner.
The captured archive passed integrity checks; this is a recovery limitation,
not an observed explanation for current missing rows.

The temporary overnight registry worker is also outside the versioned deployment.
Its cooldown/lock recovery repairs helped that run, but are not maintained
scheduler guarantees until moved into tested runtime code.

## 6. Health reporting measures execution more than completeness

[`run_cycle`](../src/swingset/schedule/cycle.py#L225) defines settled as an empty
`pending_work` table and can then publish. Due watches, never-fetched URLs,
unresolved mappings, and active findings do not make it unsettled. Its aggregate
parse-failure flag is calculated after the publication step; it is not a coverage
gate. Request-budget skips are omitted from the checked count and have no
dedicated reason counters in the cycle summary.

The [doctor and daily summary](../src/swingset/cli.py#L100) expose budgets, watches,
work counts, findings, publication, and backup status. They do not measure the
oldest unresolved gap, progress by acquisition class, or a cursor whose successful
requests never advance it. The review queue describes problems; it does not
enqueue repair actions. Possible/unmatched identities and zero-candidate registry
joins are not fully represented there.

Findings generally close when their owner reruns without the problem. Registry
comparison findings depend on rerunning the crosscheck, which was scheduled at
initial sweep completion. They can remain stale after a later canonical repair
because there is no periodic comparison reconciliation.

## Recommended repair order

1. **Fix registry liveness.** Separate successful-check freshness from content
   provenance. Verify repeated identical not-found probes advance, repeated
   unchanged profiles rotate through stale refresh, and late first-point numbers
   are discovered after the initial sweep.
2. **Fix identity semantics.** Address unrestricted divisions and mixed-person
   names, validate judge-specific scoring, and schedule bounded verification for
   source-asserted IDs lacking dancer records. Replay with explicit versions.
3. **Guarantee useful collection capacity.** Reserve host budget for new pages and
   indexes, bound date-less polling, and expose oldest-due age and skip reasons.
   Continue draining the existing scoring.dance backlog within source limits.
4. **Make gaps actionable.** Track each supported gap class with its evidence,
   next safe action, last attempt, retry time, and resolution condition. Distinguish
   retryable, waiting-for-source, needs-code, needs-review, unavailable, and
   out-of-scope states. Reconcile this inventory independently of pending work.
5. **Add missing evidence paths.** Prioritize historical event enumeration and
   mappings, supported URL seeds/adapters, and archived-layout parser repairs.
   Unknown URLs and unavailable source fields need explicit coverage limits.
6. **Contain failures and verify convergence.** Add bounded failed-work recovery,
   artifact restore/refetch handling, recurring finding reconciliation, and alerts
   for no progress despite successful cycles. Publish coverage/freshness alongside
   integrity, and distinguish acceptable incompleteness from regressions.

The appropriate target is measurable recovery for supported, obtainable data.
Unpublished results, masked identities, and unresolved contradictory sources
cannot responsibly be driven to a blanket 100% completeness figure.

## Reproduction

Run the [offline checks](self_healing_checks.py) from the repository checkout:

```sh
nix develop --command uv run python research/self_healing_checks.py
```

The script uses temporary SQLite state, checked-in response fixtures, mock HTTP,
and synthetic identity candidates. It prints probe freshness, stale-profile
reselection, and the judge score; it makes no network requests. Saved results and pinned judge
statistics are in [verification/2026-09-12/self-healing](verification/2026-09-12/self-healing/README.md).
