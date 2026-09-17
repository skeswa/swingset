# Completion-based request spacing — 2026-09-17

The acquisition audit identified a gap between request grants and dispatch:
variable reservation and receipt work could make two recorded issue times slightly
less than the required interval apart. The coordinator reported two intervals
9 and 3 milliseconds below ten seconds. This change does not edit those retained
receipts or claim they satisfy the spacing rule.

## Implemented locally

`fetch/politeness.py` now retains a schema-28 per-host reservation with the
original effective gap: configured minimum, applicable ordinary/sweep floor,
and robots crawl delay. Release advances the host deadline to at least observed
completion plus that gap, keeping an already later deadline. Failures, redirects
and robots requests use the same boundary. Budgets retain their original debit
semantics and response bytes retain their original request day.

A shared mutex and weak live-owner registry exclude a second Gate or FetchClient
using the same state database in the writer process. This does not merge budgets
or reservations from unrelated databases. Read-only `assess` does not mutate or initiate
recovery. An abandoned durable reservation has no trustworthy completion time;
`acquire` begins a fresh full monotonic wait using its retained gap. A crash
during that wait restarts it. The same fresh wait applies to completed retained
reservations when no local monotonic deadline survives. A forward UTC clock change
after restart cannot replace that wait. Distinct SystemClock objects share the
process's monotonic domain; an unrelated injected clock does not. The wait consumes no extra request or byte debit.
The reservation is replaced only in the next request's admission transaction.

The review also found that the ordinary client computed its byte debit day after
grant processing. That could cross midnight after the request debit. The grant
now carries one UTC accounting timestamp through the scheduler/origin receipt
and client day. Release independently derives the byte day from the durable
reservation. Acquisition uses that same captured instant for daily budget
assessment and debit, avoiding two clock reads straddling midnight. These remain
accounting timestamps, not measured socket dispatch times.

Schema 28 marks paid legacy hosts as unknown. It does not translate an expired
grant deadline into completion. `Gate.establish_spacing_baseline` accepts only
an unknown legacy paid host, inside a writer transaction, with no live reservation
or unsettled request admission. It requires an observed stopped-at time, a gap
covering the configured ordinary floor, and an evidence reference. It appends an
immutable baseline receipt and starts no HTTP work. The next acquisition still
waits the full gap. A rolled-back baseline grants no authority.

## Operating baseline procedure

The coordinator must retain exclusive writer ownership and confirm the previous
worker is stopped before adopting legacy hosts. Inspect existing host deadlines,
effective configuration, and retained robots evidence. Supply a conservative gap
covering the applicable configured floor and known robots delay, with a retained
evidence reference and observed stopped-at time. Unknown robots requirements need
explicit accounting; the API does not derive or claim that evidence itself.
Record each baseline in a database transaction and retain its returned receipt
identifier. Existing operator holds, cooldowns and budgets remain in force.

The API is an authorized operational reconciliation tool, not automatic permission
to clear an unknown host. Its caller owns the evidence review. No production
baseline has been established by this implementation task.

## Validation

The initial focused run before legacy adoption handling passed 72 tests in
4.83 seconds. After adoption handling, the new request-spacing file passed
12 tests in 0.94 seconds. These are separate source states, not one combined run.

New checks cover actual mocked HTTP starts after variable grant latency; robots
and original crawl delay; multiple live gates; forward wall-clock change with
monotonic waiting; ordinary and registry sweep floors; a real subprocess exit
after a durable grant; release transaction failure; original debit day; schema
upgrade; reviewed baseline requirements; and baseline rollback. The HTTP times
are transport callback observations, not operating-system socket timestamps.

The first broader run after fail-closed legacy handling passed 124 checks and
failed nine H14 cases that inserted old paid budgets without completion evidence.
Those setups now establish explicit offline baselines before testing daily caps
and remaining midday capacity. Their budget, spacing and service assertions were
preserved. Current-byte reruns and independent review are recorded below when
complete; no integrated full-suite result is claimed here.

After the midnight and clock-domain fixes, the request-spacing, fetch-control
and H14 acceptance groups passed **51 tests in 4.30 seconds**. Ruff passed for
the three touched fetch modules and two test files; mypy passed for the three
fetch modules.

The final broader focused run passed **169 tests in 23.54 seconds**:

```sh
.venv/bin/pytest -q tests/test_request_spacing.py tests/test_fetch.py tests/test_fetch_controls.py tests/test_fetch_eligibility.py tests/test_event_timing.py tests/test_h14_acceptance.py tests/test_event_turns.py tests/test_event_capacity.py tests/test_history_origin_dispatch.py
```

The independent reviewer inspected shared ownership, release and admission
rollback, legacy baseline authority, original debit day, restart and clock-domain
behavior. Their final run of `test_request_spacing.py`, `test_fetch_controls.py`
and `test_event_turns.py` passed **41 tests in 5.12 seconds**, with unchanged
before/after hashes for the six reviewed source/test files. No blocking finding
remained. This count overlaps the broader run and must not be added to it.

The local pre-format slice digest is
`25980e4ecc187b76e0f0c02e349cdd2456cab86fa8fafc0a7b02e8fcfa7e8adc`.
It is SHA-256 of sorted lines containing each file's SHA-256, two spaces,
repository-relative path and newline, for the three touched fetch modules,
`state/db.py`, migration `0028_request_spacing.sql`, `test_request_spacing.py`
and `test_h14_acceptance.py`. This is a focused slice identifier, not the
coordinator's integrated source freeze. Formatting needs a fresh integrated receipt.

A separate expanded run exposed that the maintained fixture accounting helper's
SQLite authorizer did not allow the new spacing table. The coordinator owns that
helper update and its separate tests. Retained fixture wrappers and evidence are
unchanged by this task.

## Limits and stage

Implemented locally; deployment and publication are pending. The prior frozen
candidate and its schema-27 rehearsal remain separate. Schema 28 needs a new
integrated source freeze, validation and rehearsal before deployment.

Completion-based spacing costs response duration plus the full gap. It does not
prove remote server processing has stopped after a client crash. Exclusive writer
ownership and stopped-worker evidence remain required for recovery. Unknown legacy intervals are not
converted into exact timing. Monotonic local waits do not manufacture historical
socket timing or production throughput.

See [D-0056](../../decisions/0056-anchor-host-spacing-to-request-completion.md).
