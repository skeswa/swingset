# Event retirement and recorded history, 2026-09-17

Implemented locally at schema 27. This record does not establish deployment,
production calibration, owner acceptance or publication. The coordinator owns
integrated validation and operating receipts.

## Behavior

An immediate page-withdrawal proof does not alone retire an event. The additional
proof requires a prior positive event declaration, the same owner's admitted
authoritative omission, and no surviving independent declaration anywhere in
the completely checked retained accepted source domain. An empty declared group
still declares the event. Every inspected generation verifies its retained
artifacts, current reviewed policy and enumeration-bootstrap disposition.

The domain uses retained recipe source identity, not mutable watch ownership.
It is capped at 128 generations and shares ordinary bounded verification
resources. Exhaustion, missing evidence, pending bootstrap, an old unsupported
schema or an unproven immediate edge remain unknown. Unknown pagination remains
unknown. Sources exceeding the bound do not gain an absence claim from a sample.

Schema 27 separates immutable source-event withdrawal receipts from immutable
verification proofs. Revalidation against new unrelated declarations can produce
a new proof, but the same withdrawn enumeration records one retirement. Current
reporting also checks source revision, admission journal high-water mark, exact
page-proof binding and ordinary event/policy freshness. Retirement records no
successful progress and grants no publication authority.

Fleet catalog pages add separate `current_state` and `state_counts` fields:
`locally_accounted`, `waiting`, `explicitly_retired`, or `unassessed`. Existing
accounting assessments and scoped counts remain available. Locally accounted
does not mean all source interpretation or publication succeeded. Historical
reopening remains a recorded transition, not an inferred lifetime count.

`swingset.schedule.event_history.report` and its module command provide four
bounded streams: accounting, enumerations, page retirement and source-event
retirement. Each page returns at most 100 records, identifies its high-water
mark and gives counts only for returned records. Reuse `through` and advance
`after` to the returned cursor. New appends cannot enter the pinned history.
The report names the first recorded receipt and explicitly leaves prior legacy
and unobserved history unknown. Reads do not verify artifacts or mutate state.

## Validation

The focused retirement/history/accounting, two existing restore groups and the
independent extension acceptance file passed 83 tests in 7.20 seconds before
coordinator formatting. This was the command:

```sh
.venv/bin/pytest -q tests/test_source_event_retirement.py tests/test_event_history.py tests/test_event_retirement.py tests/test_event_accounting.py tests/test_event_accounting_restore.py tests/test_event_completion_restore.py tests/test_event_extension_acceptance.py
```

New tests cover actual admitted event withdrawal, independent empty declarations,
mutable watch ownership, missing independent bodies, unprocessed admissions,
bounded-domain exhaustion, admission-journal-only invalidation, immutable
receipts, revalidation without duplicate transitions, pinned history pagination,
legacy schema and invalid cursors. Ruff and mypy passed the four reporting and
verification modules after the admission-fence and state-label changes. The
coordinator must validate final integrated bytes after formatting.

A [source-bound check receipt](../../evidence/recovery/event-history-2026-09-17/local-check-001/checks.json)
records 83 tests passing in 7.08 seconds, Ruff and mypy passing, and identical
source/test hashes before and after all three commands. This receipt precedes
coordinator formatting and is separate from any later integrated full run.

The subsequent [final local check](../../evidence/recovery/event-history-2026-09-17/local-check-002/checks.json)
adds a command test while the writer lock is held. The read-only history command
does not acquire that lock. All 84 tests passed in 7.25 seconds, Ruff and mypy
passed, and source/test hashes stayed unchanged. The first receipt is retained.

Independent review by the timing agent found no blocking issue after the
admission high-water fence. Its retirement, history and extension acceptance
selection passed 52 tests in 3.88 seconds before the final lock-free CLI seam.
It inspected that seam; the later source-bound 84-test receipt includes its test.
Its separate acceptance tests exercise activated restore of the withdrawal
receipt and current proof; see the
[extension acceptance audit](event-extension-acceptance-2026-09-17.md) for its own
run receipt and remaining operating gates. Historical completed tests are not
retroactively evidence for these new bytes.

## Remaining limits

Complete recorded history is available by explicit pagination; complete lifetime
fleet history remains unavailable. This implementation does not infer skipped
retirements or event removal from an empty denominator. A fresh production
migration rehearsal, current backup/restore checks, bounded observation and a
subsequent independently audited acknowledged release remain separate gates.
Pagination cursors belong to the same retained database and its physical
backup/restore lineage. They are not portable identifiers for an arbitrarily
reconstructed database or a maintenance operation that changes SQLite row IDs.

See [D-0051](../../decisions/0051-prove-event-withdrawal-and-page-recorded-history.md).
