# D-0216: The work queue leases, backs off and quarantines

Recorded: 2026-09-27  
Decided by: agent, 2026-09-27, under D-0175; source: phase 2 of the [architecture plan](../../docs/plans/architecture.md) (findings F9, F14, F15)  
Topic: Scheduling  
Supersedes: in part, [D-0211](0211-a-derivation-that-raises-retries-on-a-clock.md) (a unit no longer retries forever) and [D-0214](0214-a-derivation-backlog-does-not-pause-acquisition.md) (backpressure counts only runnable parse work)  
Superseded by: in part, [D-0249](0249-a-waiting-failure-with-the-same-message-is-a-repeat.md) (a waiting failure with the same message is a repeat)

## Decision

`derive_one` leases its unit in `work_queue` in a short claim transaction
committed before its admission (`<loop>:<run_id>`, 30 minutes). A lease that expired, belongs to this
process's own loop, or meets the exclusive process is dead: the next lease
closes its running attempt as interrupted and settles its admission. Success
deletes the row. A failure's class is its reason, plus the exception type for
`unit_exception`. Failures of one class on one input fingerprint count up;
either change restarts the count. The count sets a backoff of 15 minutes
doubling to 6 hours; the third failure quarantines the unit for 7 days, the
twentieth for `unit_io_error` (retried after a minute before that). A blocked
parse or a missing artifact waits for input 24 hours doubling to 7 days and is
retried when its clock comes due; a retry that ends the same way counts as
`waiting_repeats`, not a failure. `publication_fenced` and
`write_lock_timeout` (a busy timeout) retry in 60 seconds and are never
counted. A unit whose process died inside its admission or its attempt (a
kill, an OOM) fails with class `process_interrupted`, closed by the loop's
recovery or a takeover, and so quarantines at the third; a dead run's lease
with no running attempt is such a death. A tick killed after systemd asked
it to stop leaves `runs/<run_id>.stop`, and its death is not counted. An
exception in the admission (capture, attempt) is a `unit_exception` failure
recorded on the queue row alone, and selection honours that row; one that
escapes `derive_one` is the unit's counted `unit_exception` too. The cycle
keeps its own recovery: it releases every lease and retries an interrupted
attempt in a minute, uncounted. Acquisition
backpressure counts only parse units derive could run now. `swingset queue
requeue` resets matching rows; the maintain loop deletes rows whose unit has
nothing left to do. The watchdog prints `[unit-quarantined]` and
`[lease-stuck]`; `[unit-failing]` is retired.

## Why

A failing unit retried every cycle forever and failed every cycle (D-0211),
and a blocked parse was never retried at all. Blocked parses still counted
toward backpressure, so enough of them stopped fetching (F9). Loops need a
lease to share derive work safely (F14). A linker bug could quarantine
thousands of units, so the alert aggregates past five (F15). The phase 2
review found that only exceptions inside the processing transaction were
counted: a unit whose capture raised, or that killed its own process (on the
8 GB worker, an OOM), was picked first on every tick and never quarantined.

## Alternatives and consequences

A quarantined unit waits up to 7 days for a fix unless its input changes or
an operator requeues it. Blocked parses now cost one retry per unit per day
to week.

## Links

- [State: work queue](../../docs/reference/state.md#work-queue)
