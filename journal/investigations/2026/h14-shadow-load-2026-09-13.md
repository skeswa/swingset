# Measuring scheduled work without activating it (H14)

> Historical record. Dates, status claims, and instructions below describe the
> original work. See [current status](../../../docs/status.md) before acting.
> [Project journal](../../README.md)

Fair scheduling shares time between competing tasks. This record concerns measured service or deployment checks. The original work ID is H14.

Measured from one read-only SQLite snapshot of the held production schema 11
state at **2026-09-13 10:10:31 UTC**. No runtime module was imported, no HTTP
request was made, and no semantic state was changed. The scan took 0.55 seconds.
This is retained demand during an intentional operational hold, not a measurement
of steady-state throughput or achieved scheduling fairness.

Rebuild with [h14_shadow_load.py](../../tools/runtime/h14_shadow_load.py), using the service Python:

```sh
python -m journal.tools.runtime.h14_shadow_load --state /var/lib/swingset > /tmp/h14-load.json
```

For a checkpoint, add `--checkpoint`; it uses `mode=ro&immutable=1`. Live state
uses `mode=ro` and one explicit read transaction so committed WAL rows are visible.
Both connections close explicitly. Configuration bytes are read from the selected
input bundle and checked against accepted input digests.

[Machine receipt](../../evidence/runtime/h14/h14-shadow-load-2026-09-13.json), SHA-256
`ac53316495c73ba98267d28ef351186406cb2e57e56feacc82fc83b67ec121a9`.
The accepted input bundle is
`184923097435f167078a7743568eb5d6a3b962b1b40542c106164ef71b6eba78`.

## Observed demand

There are **35,417 watches**. The following counts meet the existing enabled
source, watch-state, and due-time filters. Operator holds, cooldowns, budgets,
history acceptance and admission remain additional gates; these are not counts
of requests authorized now.

| Transport host      | Due watches | Main retained demand                                                                              |
| ------------------- | ----------: | ------------------------------------------------------------------------------------------------- |
| scoring.dance       |       4,244 | 3,757 never-fetched rounds; 369 old rounds; 64 event pages without mapped dates; 54 other watches |
| points.worldsdc.com |         188 | 168 confirmation watches and 20 probe watches                                                     |
| eepro.com           |           1 | Event index                                                                                       |
| Other hosts         |           0 | Retained evidence or future/disabled/sealed watches                                               |

The scoring.dance remainder includes 33 watches still labelled dormant whose
stored activation times are now overdue. Refresh date-based policy before
classifying work; retained labels are not current event truth.

There are 3,764 ready `round_observations` requirements and six ready
`source_id_checked` requirements. Another 2,673 first-point requirements wait
for source evidence; they do not imply 2,673 known URLs or direct ID lookups.
There are 64 source-event mapping reviews. The 64 dateless scoring.dance event
watches are still labelled live: even hourly polling alone would demand 1,536
requests/day against an 800/day host limit. A bounded metadata policy is needed
before allocating more capacity.

The offline queue currently has **zero pending units and zero pending parse
bytes**. H12 has no attempt rows yet; this census cannot establish an observed
backpressure threshold or sustained processing rate.

Historical inventory retains 32 calendar CDX captures across three URLs and 15
completed CDX query records. Seventeen years have open `phase1_incomplete`
findings; **zero years are accepted**. Seven acquisition-gate findings remain.
These findings are unresolved coverage, not permission to create phase-2 watches.
Imported sealed captures can have no live watch `last_checked_at` despite retained
snapshots; the script excludes those from its proposed new-page class.

## Existing capacity and observed costs

Costs below are accounted response-body bytes divided by actual durable host
request charges, September 7–13 inclusive; September 13 is partial. They include
the retained bootstrap and manual intake workload, and are not wire-byte or
request-latency measurements. Claim-body percentiles in the JSON describe
retained snapshots, which exclude many unchanged checks.

| Host                          |               Existing daily request limit | Used September 13 | Observed requests in window | Mean accounted bytes/request |
| ----------------------------- | -----------------------------------------: | ----------------: | --------------------------: | ---------------------------: |
| scoring.dance                 |                                        800 |               725 |                       2,400 |                       66,180 |
| points.worldsdc.com           | 1,500 ordinary; 20,000 existing sweep mode |                 6 |                      29,539 |                        4,985 |
| eepro.com                     |                                        600 |                 2 |                         730 |                       23,422 |
| scores.worlddanceregistry.com |                                        400 |                 0 |                          34 |                      146,507 |
| worldsdc.com                  |                                         10 |                 3 |                          10 |                      109,439 |
| www.worldsdc.com              |                   200, default host policy |                57 |                          57 |                      260,333 |
| web.archive.org               |                                    **200** |           **200** |                         200 |                       67,315 |

Archive has no capacity left on this recorded UTC day. Scoring.dance has 75
requests left; it used all 800 on September 12. Old sweep usage must not become
ordinary registry capacity. Hosts retain their separate counters, gaps,
automatic pauses and any byte limits. Danceconvention's configured 200/day and
300 MB/day remain unchanged, although its adapter has no retained demand here.

The largest retained network body is 11,439,675 bytes from scoring.dance; its
95th percentile is 136,451 bytes. WDR's largest is 820,454 bytes and Archive's
largest is 267,906 bytes. None is a guaranteed upper bound on future responses.
Network duration is unmeasured; run durations mix local work, waits and requests.

## Initial conservative objectives

These are proposed starting parameters grounded in the census, not achieved
service levels or statistical estimates of optimal shares. Shadow receipts must
separate admitted attempts from verified completions.

| Allocation    | New pages | Current refresh | Identity confirmation | Old evidence |
| ------------- | --------: | --------------: | --------------------: | -----------: |
| Default host  |       40% |             30% |                   20% |          10% |
| scoring.dance |       50% |             25% |                   10% |          15% |
| Registry      |       10% |              0% |                   70% |          20% |
| Archive       |       50% |             10% |                   20% |          20% |

Unused shares are borrowed only by otherwise eligible work. Shares never enlarge
a host budget or force a request. Registry's existing bounded probe and trickle
policies remain authoritative. Archive reservations become usable only after
the independent history gates permit the particular work.

At scoring.dance's unchanged limit, the starting reservations correspond to
400 new-page and 120 old-evidence attempts/day. Those reservations give both the
3,757-page acquisition backlog and the 369-page old-round backlog service under
sustained demand. Dividing backlog by these numbers is not an ETA: requests can
fail, corrections can reopen work, and the discovery universe changes.

Use age promotion with an initial **24-hour eligible service-gap objective per
host/class**, while measuring individual-item wait separately. This promises
class service, not a daily check of every old item. Round-robin eligible hosts
and offline stages prevent one origin or parse stage taking the whole cycle.
Skip temporarily cooled or budget-exhausted hosts before a visit; the HTTP gate
still rechecks and charges every actual request.

For a 720-second cycle, start with soft reservations of **30 seconds for
reconciliation, 300 for acquisition, and 390 for offline work including build**.
Unused time is borrowable; an admitted atomic unit keeps the H13 write deadline.
Historical acquisition receives an explicit allocation even when ordinary due
work exists, while retaining its year, source, pause, capture and host gates.

Start backpressure at **128 MiB pending parse bytes, 1,000 pending parse items,
or 10,000 total offline units**. These are initial high-water marks, not measured
steady-state limits. At the observed scoring.dance 95th percentile, 1,000 bodies
are about 130 MiB. Reserve at most **two issued HTTP requests per cycle** for
identified unblock requirements while ordinary collection is held. Admit the
next ordinary request only below the marks; report any overshoot from an
already-admitted response. Future response sizes are not bounded by this census.

Dateless metadata gets three ordinary recovery attempts, then a 30-day recheck;
gone/unpublished evidence gets a 90-day recheck or a retained trigger. New parent
evidence and recovered actual dates can reactivate work. Do not invent event
dates or leave unknown dates on the live polling clock.

## Implementation handoff

Keep the allocation policy and durable service counters separate from the
cycle driver. The policy proposes an eligible watch or offline stage; the
existing HTTP, retry, pause and history gates retain final admission authority.
Record service only after an issued attempt, and persist counters across restart.
Resume preserves today's used budgets and carries no accumulated burst credits.

Validate sustained current demand alongside old work, a cooled host beside a
healthy one, saturated offline backlog with reserved unblock acquisition, and
restart after a long pause with depleted request/byte budgets. Report actual
service gaps and backlog alongside the configured objectives before calling the
shares successful. H13 remains independently pinned for production rollout.

## Local implementation evidence

`schedule/fairness.py` attributes actual HTTP issuance atomically with the
existing host-budget debit. Robots, redirect hops, retries and failed issued
requests count. A pause or denied visit counts zero. Pressure-reserve exhaustion
removes further offers for that run before selection, and the request admission
rechecks it. Stage and unit-kind rotation survives restart without satisfying
an unmet output requirement.

The retained-state selection measurement in
[`verification/h14-picker-cost-2026-09-13.json`](../../evidence/runtime/h14/h14-picker-cost-2026-09-13.json)
uses all 35,417 watches and accepted configuration in a read-only SQLite
snapshot. On schema 12 it supplies empty in-memory temporary attribution
counters, since the production ledger does not exist yet. The final formatted
policy measured 0.371 seconds, about 7.4% of the ordinary five-second host gap.
This is one selection cost; it does not establish throughput or eligible
service gaps. `h14_picker_cost.py` rebuilds the measurement and records code
hashes. It issues no HTTP and never writes the main database.

Offline tests exercise sustained current and old demand, real HTTP debit
accounting, partial-day quota exhaustion, paused and cooled hosts, the two
request repair reserve, restart rotation, retry latches, and captured scheduling
changes that do not invalidate interpretations. Doctor separates initial
objectives from observed host usage, attributed requests, body bytes and
offline attempts. Wall time since a request includes intentional pauses and
is not labeled verified progress or eligible lag.

One inherited retry detail remains for H15 dependency precision: the H12
project-work fingerprint includes the whole accepted `config/sources.toml`
digest. A scheduling-only edit therefore permits one retry of an already
blocked project unit, although it queues no new interpretation work and does
not relabel existing source observations. Parser retry identity is unaffected.
Narrow semantic configuration dependencies belong to the next materialization
step; H14 does not silently change that existing fingerprint contract.
