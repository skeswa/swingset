# Verify a 33-page event under competing work

Date: 2026-09-16 UTC  
Type: Offline integration acceptance; no production throughput claim

The test in `tests/test_event_completion_cohort.py` combines admitted parent
enumeration, the normal scheduler, mocked HTTP through `FetchClient`, gated
parsing and admission, and artifact-backed local accounting. The 33-page event
has no canonical mapping. A dated current event stays due, and each selection
adds another unfetched index. Target watches are never manually sealed.

## Results

All 33 pages were acquired and interpreted after 405 selections and 406 issued
requests, including robots. The request ledger contains 232 new-work and 174
current-work requests, matching the configured 40:30 weights. Listed-page work
received 133 new-work requests and discovery received 99. Every request and byte
was charged; the five-second host floor held. An actual operator pause prevented
requests and preserved paid usage and turn state.

The fixed cohort's maximum observed gap was 79 requests. The regression bounds
are 600 selections, 601 requests, and a 128-request gap. These are explicit
fixture limits, not a derived general guarantee or calibrated operating
objective. The fixture uses a 10,000-request daily cap so the finite experiment
does not stop at a daily boundary. It reached 539 watch rows and 308 due watches;
306 of its 405 arriving indexes remained unfetched at cohort completion.

Local artifact verification confirmed 33 acquired, 33 interpreted, and all known
pages accounted for. Pagination, eligible age, and published progress remain
unknown where unsupported. Fifty-two bounded observation visits completed the
sampled accounting pass. Three pressure refreshes reduced the measured backlog
from 101 to 77, below the low watermark of 80, and a previously deferred index
then issued one additional request. No watch was deleted to lower pressure.

An initial fixture incorrectly let its live competitor become metadata work.
The final fixture gives only that competitor a dated canonical event; all
results above come from the corrected run. Earlier bounds are not accepted as
evidence for current-class competition.

## Validation and limits

The agent's related run passed 67 tests in 24.39 seconds. After `mise run fmt`,
independent coordinator validation passed this test and five actual activated
restore tests in 17.56 seconds, plus Ruff and mypy over 206 source files. The
[receipt](../../evidence/runtime/event-completion-2026-09-16/new-acceptance-validation-001.json)
pins both new test files and all logs. It also verifies that the 552 files
covered by the earlier full 2,063-test run are unchanged. The six additional
tests were run separately; this is not a new full-suite run.

The experiment does not measure network throughput or prove continuous fleet
completion. It does not cover day reset, failure alarms, the complete cycle,
linking, build, or publication. Actual disposable restore and accounting
invalidation are exercised separately in `tests/test_event_accounting_restore.py`.
Eligible-time reporting, unsupported-page classification, deployment, measured
ordinary-host service, and publication of the extension remain pending.
