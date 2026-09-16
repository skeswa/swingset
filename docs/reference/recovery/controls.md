# Pausing work and explaining progress

Operators need to stop affected work and see what remains. These rules define pause scope, bounded response, and status reporting.

[Overview](../recovery.md) · [Current status](../../status.md)

## 8. Operator control

These are changes to the pause, doctor, and summary contracts in
[operations](../operations.md#locks-and-operator-commands). No second control
system or status command is added.

### Pause and resume

`operator_pauses` gains the scope kind `kind` (a requirement kind) and the
columns `pause_id`, `actor`, and `control_revision`. `swingset pause` and
`swingset resume` gain `--kind <requirement kind>` and `--reason`. Selectors
use registered identifiers listed by doctor; unknown selectors are rejected.

| Selector       | What it pauses                                                                                        |
| -------------- | ----------------------------------------------------------------------------------------------------- |
| `--all`        | Requests to every host and repairs of every kind.                                                     |
| `--host <h>`   | Requests to that host. Post-fetch stages continue, as today.                                          |
| `--source <s>` | Requests to that source and repairs whose subject belongs to it.                                      |
| `--kind <k>`   | Repairs of that kind everywhere: acquisition, reparse, admission, derivation, and repair publication. |

`--all` and `--source` now hold matching repairs as well as requests; that is
the intentional change to the rule that post-fetch stages always run. Use
`--host` to stop collection while letting an override publish. `resume` removes
only the matching operator pause and never clears an automatic host block,
cooldown, or budget limit. Overlapping pauses combine. Indefinite pauses survive
restart, timer runs, deployment, and backup restore. An expired timed pause is
recorded as expired, not erased from control history.

The gate runs at every automatic entry point: before each request, before each
bounded offline unit, and before each publication commit, so a normal cycle
cannot pick up a paused repair under another name. Shared work touching a
paused scope waits as a whole when it cannot be split safely; doctor exposes
that dependency. Reconciliation, doctor, and journal acceptance remain
available while paused; they may reveal work but cannot execute paused repairs.
Publication safety checks and suppression stay mandatory. A pause cannot
authorize an unsafe release: if a release needs a paused correction, hold it
and report the pending correction and its age. Resuming the scope publishes it.

Persist the control change before acknowledging it. Doctor distinguishes
`running`, `pausing` (new work gated, active work draining), and `paused` (no
matching attempt in flight). An already-started request or atomic unit may
finish; its follow-up work is gated. Serialize work admission with the control
transaction so work cannot start after a committed pause using a stale check.
An uncertain publication response remains draining until receipt reconciliation
establishes the outcome. `pause --wait` with a bounded timeout waits for
`paused` and on timeout reports what was persisted and which attempts still
drain. Split long cycles into bounded units with checkpoints so control
mutations are serviced between them within a configured bound; exceeding it
produces a stuck-drain status. This changes the whole-cycle writer lock
contract while retaining one data writer and atomic transactions.

Pause state is an execution constraint, not requirement state. Retain
requirements, retry deadlines, attempt history, scan cursors, staged input
manifests, fingerprints, and publication receipts. Commit each checkpoint with
the output it covers. Resume revalidates desired inputs and continues from the
last committed boundary; incomplete units may repeat, completed units are not
replayed. A source change can require restarting a finite enumeration, with its
prior work and reason retained. A stale cursor never skips pages. Resume has no
catch-up, as today: elapsed pause time does not replenish a day's used budget.
Requirement age and evidence staleness keep increasing while paused, and doctor
reports paused duration separately from eligible execution time.

### Doctor and summary

`swingset doctor` reads one consistent local snapshot without the writer lock,
as today, and gains `--json` (a versioned schema with the same facts),
`--watch` (refresh within a configured interval, default five seconds),
`--source`, `--kind`, and `--requirement <id>` for drill-down. It shows the
snapshot time, last reconciliation time, scan cursor, and whether the view is
stale; a stopped worker does not stop doctor from reporting staleness.

| Doctor section           | Required information                                                                                                                                                                                   |
| ------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Control                  | Effective running, pausing, or paused state; matching pause IDs, reasons, expiry, draining attempts, and the exact resume selector; an enabled but idle or blocked worker is distinguished.            |
| Work by source and kind  | Unique requirements by state; eligible, active, and pause-blocked counts as labeled overlays. Oldest unresolved age, last verified progress, next eligible action, and dependency blockers.            |
| Throughput               | Verified requirement completions over the last hour and day, with wall-clock and eligible-time rates labeled; host budget remaining and next reset.                                                    |
| Pipeline and publication | Acquired, interpreted, admitted, derived, and published scope counts with units and cutoffs; latest release ID and time; supported repairs awaiting publication and the oldest pending correction age. |
| Attention needed         | Oldest and highest-impact guard failures, review requests, unavailable evidence, stuck drains, and pending corrections, each with evidence and a next action.                                          |

`swingset summary` gains a change-since-last-report section (newly opened,
reopened, satisfied, and retired requirements, with attempts and failures
listed separately, plus policy changes and scope transfers) and `--since`.

Persist requirement transitions and control events so reports survive
restarts. For a fixed filter and interval, opening unmet count plus newly
opened and reopened requirements minus satisfied and retired requirements
equals closing unmet count. Record transfers when policy or subject keys
change; do not manufacture progress by deleting requirements. Keep unique
repair counts separate from affected-row counts: one event mapping may repair
thousands of joins and is one requirement.

Continuous discovery makes the live backlog a changing total. For bounded work,
capture a cohort and report how many baseline members currently satisfy the
pinned rule out of the baseline total, beside newly discovered work outside the
cohort. Reopened members reduce that percentage. Retired or out-of-scope
members stay visible and are not successful repairs. A changed policy creates a
new cohort or an explicit incompatibility label, never a silent denominator
change. Local repair completion and publication completion are reported
separately; a repaired join waiting for a release is not yet fixed for dataset
users. Unknown source universes, including all possible future WSDC IDs, have
no completion percentage; state the enumerated range or cohort instead.

Offer an ETA only for a bounded, automatically actionable cohort with enough
measured throughput. Label it an estimate with its observation window and
budget constraints; report `unknown` while paused, stalled, waiting for source
evidence, or dependent on review. Intentional pauses suppress eligible-work
no-progress alarms for their scope, but not evidence-age, pending-correction,
stale-status, or stuck-drain warnings.
