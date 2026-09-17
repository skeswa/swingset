# Bounded acquisition clocks and conservative local alarms

Date: 2026-09-17 UTC  
Status: Implemented and tested locally; not deployed or published  
Decision: [D-0044](../../decisions/0044-observe-bounded-acquisition-waiting.md), proposed detailed implementation choice

## Result

The ordinary cycle now observes a bounded group of verified missing acquisition
obligations. It closes timing intervals around scheduling and HTTP admission,
shares host gates with actual request issuance, and checks robots from bounded
verified local bytes. Schema 26 stores counters, receipt frontiers, episode
identity, per-run summaries, and observation rotation. The event doctor includes
these diagnostics. The integrated repository may have later migrations from
other independent work; 26 identifies this increment's migration only.

Failed requests reset selected service age, never successful-progress age.
Ordinary failed-response evidence revisions preserve the clocks after bounded
reverification. Initial old successful operations do not fabricate progress or
suppress every new alarm. Unknown pending success suppresses progress alarms
independently of service alarms. The last remedy names an actual selected request
receipt, or is null when no such remedy is recorded.

Observed waiting remains a lower bound when gate history has gaps. A current
eligible event may report `lower_bound_exceeded` once already closed eligible
waiting exceeds its configured objective and all possible intervening resets
remain accounted for. Exact age remains unknown. Changed enumeration, input,
epoch, or policy identity retains prior counters but invalidates inherited alarm
bounds until a new corresponding receipt establishes continuity. Both production
objectives default to unset.

See [the exact contract](../../../docs/reference/event-timing.md) for scope,
fields, bounds, and commands.

## Independent review and repairs

An independent agent reviewed the timing and request-gate changes. Review found
and implementation corrected:

- Old pre-episode successful operations could suppress progress alarms forever.
  The durable episode operation frontier now separates them from new uncertainty.
- A failed-response revision could restart the episode and indirectly reset
  successful-progress waiting. Bounded reverification preserves that waiting.
- A pause could commit after gate assessment but before the opening sample.
  Bracketing assessment with control/marker/dependency observations prevents
  crediting that paused interval.
- Real bookkeeping consumes elapsed time, making exact coverage incomplete.
  Separate continuity flags now permit a sound lower-bound breach without
  pretending the missing gate intervals are known.

Review also prompted separate service/progress uncertainty, conservative handling
of malformed legacy timestamps and future robots cache metadata, and inclusion
of shared request identities and possible aggregate interpretation success.
No source request or external communication was made during this work.

## Validation

Final branch-focused command before coordinator formatting:

```sh
.venv/bin/pytest -q tests/test_event_timing.py tests/test_fetch_eligibility.py tests/test_fetch.py tests/test_fetch_controls.py tests/test_config.py tests/test_cycle.py tests/test_event_reporting.py tests/test_event_progress.py tests/test_event_completion_cohort.py
```

All **129 tests passed in 23.99 seconds**. Ruff passed for the changed timing,
fetch, scheduling, clock, and test files. Mypy passed over **217 source files**.
These are focused working-source checks, not a full integrated run or operating
acceptance. The coordinator must format, freeze the integrated source, and run
fresh checks before using later bytes for rollout.

The 25 dedicated timing/gate tests include an actual `run_cycle` with an advancing
clock: bookkeeping creates positive unknown time, yet both clocks produce a
justified `lower_bound_exceeded` result. An actual failed HTTP sequence followed
by a reverified phase retains 12 seconds of successful-progress waiting and
increases it to 24. A two-host request test credits the other eligible event
while one host is in flight and retains one selected debit owner. Further tests
cover day reset, request-day byte charging across midnight, pause races, marker
changes, robots expiry and bounds, malformed metadata, crashes, old successes,
changed dependencies, unavailable evidence bounds, and read-only reporting.

Earlier checks were separate runs: 120 and then 126 tests passed before later
review repairs. One earlier 120-test attempt failed its runtime-recipe assertion
while another edit changed source between two cycles. A stable rerun passed;
that failed attempt was not an acceptance receipt. Do not add these overlapping
runs or the dedicated checks to the final count.

The pre-format timing-slice manifest digest is
`ffeb090bc8d79928608d2d43525bd139328e229e9d0b2d8a331733a6f3b5198e`.
It hashes sorted lines of `SHA256  relative/path\n` for the following files:

- `src/swingset/clock.py`
- `src/swingset/fetch/{controls,eligibility,politeness,robots}.py`
- `src/swingset/schedule/{cycle,event_report,event_timing,event_timing_observer,fair_policy,fairness}.py`
- `src/swingset/state/migrations/0026_event_timing.sql`
- `tests/{test_event_timing,test_fetch_eligibility}.py`

This binds this local receipt's slice only. It is not the later integrated source
freeze and must not be used to validate files after formatting or further edits.

## Still open

Historical archive/origin dispatch eligibility is explicitly unknown in this
observer; existing year and source gates remain authoritative. Whole-event and
interpretation eligible ages, complete unobserved fleet history, and unsupported
legacy timing remain unknown. Events over the 32-member bound require separate
measurement coverage. Unqualified aggregate interpretations may conservatively
suppress progress alarms. SQL and filesystem operations do not have hard deadlines.

Fixed-cohort service measurements, objective calibration, deployment rehearsal,
production observation, and a subsequent coverage publication remain separate
requirements. This increment does not close V6, authorize a historical year,
change source fixture approvals, or advance published progress.
