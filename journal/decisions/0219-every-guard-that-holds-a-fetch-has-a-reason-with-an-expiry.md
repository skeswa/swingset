# D-0219: Every guard that holds a fetch has a reason with an expiry

Recorded: 2026-09-27  
Decided by: agent, 2026-09-27, under D-0175; source: phase 2 of the [architecture plan](../../docs/plans/architecture.md) ("Guards fail closed with a clock")  
Topic: Acquisition  
Supersedes: —  
Superseded by: —

## Decision

The fetch client counts every guard skip and every `Paused` grant by subject
(`host:<h>`, `watch:<id>` or `loop:<l>`) and reason. Each acquisition pass,
the cycle's or acquire's, writes them to `guard_reasons` in one transaction.
A row keeps its `since`; `last_seen_at`, `seen` and `expires_at` move. A
subject that issued a request in the pass loses its rows. Expiries, from
`since`: spacing unknown or recovery, 1 h; a `Throttled`, `ServerError` or
`Blocked` host pause, its `paused_until` plus 1 h; a spent budget, the next
UTC midnight plus 1 h; an operator pause, its end, or 48 h when it has none;
backpressure, 12 h; an event expansion denial, 24 h; publication in flight,
1 h; the release loop's low-disk refusal (`release_disk_low`), 1 h. A whole
loop tick that skips leaves its own row too: plan or release waiting for
derive to accept the inputs (`inputs_not_accepted`), 1 h; release waiting
for derive's heavy lock (`heavy_busy`), 3 h; the next tick past the reason
deletes it. History
gate reasons, `robots disallow` and `source disabled` are watch rows:
written once a UTC day, expiring in 30 days, and counted, never alarmed.
A choice of the pass that raises for any reason but contention (a busy or
locked write, a write-deadline overrun, a publication fence, an operator
pause, a control-lock timeout) leaves a
`watch:<id> request_error` row: the watch waits an hour, doubling, and a
week from the third failure in a row. The pass's flush keeps that row; only
a served request deletes it, and maintain keeps it until a week past its
expiry, so the count survives.
Waits, deadline skips, terminal watch states and request limits are not
guards. The watchdog prints `[guard-expired]` for a host or loop row seen
after its expiry within the last 2 hours, and a daily `[guards]` count by
reason; `doctor` and `summary` show the same counts.

## Why

A gate that skips every request reports no failure. "Legacy spacing
unknown" held two origin hosts for eight days before anyone saw it (D-0190).
A reason with a clock alarms in an hour. The plan says `now > expires_at`
while the reason is current; this rule reads "current" as seen after the
expiry, so a pause that ends between two passes never alarms.

## Alternatives and consequences

The scheduler already leaves out watches whose source is off, whose host is
paused or over budget, or that an operator pause, backpressure or event
expansion holds, so
in the cycle those reasons become rows only when a skip reaches the client
(a history offer, a robots check, a redirect). The spacing reasons, the
case that motivated this, always reach it. The phase 0 checks
`[gate-legacy-spacing]` and `[gate-host-blocked]` stay.

## Links

[Fetch layer, guards](../../docs/reference/fetching.md#guards)
