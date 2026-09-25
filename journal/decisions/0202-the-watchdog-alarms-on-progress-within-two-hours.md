# D-0202: The watchdog alarms on progress within two hours

Recorded: 2026-09-25  
Decided by: agent, 2026-09-25, under D-0175; source: phase 0 of the [architecture plan](../../docs/plans/architecture.md)  
Topic: Operations  
Supersedes: the two-day stale-fetch threshold added after [D-0190](0190-record-spacing-baselines-for-the-origin-hosts-from-the-eight-day-gap.md); extends [D-0188](0188-alert-on-failing-or-stalled-cycles-and-stop-re-queuing-on-config-switches.md)  
Superseded by: —

## Decision

While the worker is unheld and the cycle timer is active,
`swingset-watchdog` alarms when no request has been made for two hours
(`staleFetchSeconds`, was two days), unless every host that fetched today has
spent its budget; and when a stage with ready units has not progressed for
two hours since its work became runnable (`staleProgressSeconds`), one clock
per stage. `project/history` has its own clock from when it comes due under
its 12-hour interval (D-0199), whether or not links wait for it; links are
clocked from when history materialized or came due. Held or not, it alarms
on the size-cap pause, the two known silent gates, a host answering only 429
or 5xx, a hold older than 48 hours or of unknown age, a failed backup, and a
check it could not run. Lines carry a stable key and repeat at most every
two hours. With `alertCommand` set, a `[watchdog-alive]` line goes to it
daily. The key list is in the operation guide.

## Why

On 2026-09-25 link had not progressed since 01:32 UTC while 2,932 units
waited; parse and project kept succeeding and cycles kept finishing, so no
alert fired. History came due at 13:11 and never ran; this rule alarms at
15:11. A single clock for the whole graph would have hidden the stall until
parse and project also stopped. Requests, not stored pages, measure fetching:
on 2026-09-24 no page was stored for 17 hours while 265 requests were made.

## Alternatives and consequences

A per-guard reason table with expiry would catch any silent gate; it needs a
migration, so it waits for phase 2. Known silences until then: a blocked or
failed unit raises nothing; the ready rule skips the fingerprint test; and a
re-queue moves a unit's queue time, so a stage whose ready units upstream
churn keeps re-queuing never alarms. The progress lines fire during a
whole-graph re-derivation.

## Links

- [Operation guide, alerts](../../docs/guides/operation.md#alerts)
