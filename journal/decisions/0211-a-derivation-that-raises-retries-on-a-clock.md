# D-0211: A derivation that raises retries on a clock

Recorded: 2026-09-26  
Decided by: agent, 2026-09-26, under D-0175; source: phase 1 review of the [architecture plan](../../docs/plans/architecture.md) (I3)  
Topic: Scheduling  
Supersedes: in part, [D-0198](0198-history-no-longer-gates-acquisition-and-deadline-overruns-retry.md) (its retry clock now covers every exception, not only a deadline overrun)  
Superseded by: in part, [D-0216](0216-the-work-queue-leases-backs-off-and-quarantines.md) (quarantine after three failures of one class)

## Decision

Any exception a derivation raises ends its attempt `transient`, not
`blocked`. It retries on the D-0198 clock: 15 minutes, doubling per
consecutive failure with the same reason, at most six hours apart
(`schedule/derive.deadline_backoff`). Each retry counts as a failure in the
cycle summary, and `[unit-failing]` names a unit whose last three attempts
failed the same way. Output that differs for identical inputs
(`NondeterministicOutputError`) is one such failure. A retained page whose
parser this runtime does not know still parks for 30 days (D-0182). A
`SupersededWorkError` whose unit's input fingerprint has not moved since the
attempt began (a relabel, a fence) is `transient` on the same clock with
reason `inputs_changed_spurious`; nothing else would re-open it.

## Why

A `blocked` attempt waited for an input change that might never come, so a
deploy that fixed the code was not picked up without an operator retry, and
the failure was reported once and then went quiet (I3: every non-success has
a clock).

## Alternatives and consequences

A unit that raises for a reason no deploy fixes retries every six hours
forever and keeps `[unit-failing]` and `failed=true` on; that is the signal.

## Links

- [Scheduling](../../docs/reference/scheduling.md)
