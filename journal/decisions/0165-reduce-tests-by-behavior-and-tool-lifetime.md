# D-0165: Reduce tests by behavior and tool lifetime

Recorded: 2026-09-18  
Decided by: agent  
Topic: Test suite maintenance  
Supersedes: —  
Superseded by: —

## Decision

Consolidate equivalent test cases while preserving their observable checks.
Retire tests for completed operational tools only with their executable support,
after moving reusable safety checks to current owners. Keep distinct parser,
identity, crash, retention, fetching and publication regressions.

Distinguish the maintained suite from a routine or smoke selection. Moving tests
out of the default run does not count as deleting them. Do not hide cases in
loops or drop independent behavior to make collection report fewer than 1,000.

## Why

The owner requested a subagent review of key and nonessential tests and expressed
a preference for fewer than 1,000. The [review](../investigations/2026/pytest-suite-review-2026-09-18.md)
collected 2,998 cases. It found 36 small reduction candidates and a conditional
243-case net reduction through tool retirement. It did not establish that the
required 1,999 cases are dispensable. Repeated assertions often protect separate
implementations or different failure boundaries.

## Consequences

The review and file inventory are recorded; tests and discovery are unchanged.
The recommendation remains proposed. Replacement validation and executable
retirement are still work to do. A smaller smoke selection remains an option,
with the full suite retained as a release check.

Follow-up: the owner accepted a core default with opt-in extended coverage in
[D-0168](0168-run-core-tests-by-default-and-keep-an-extended-suite.md). That
implements the selection option; the historical-driver retirement and deletion
recommendations above remain proposals.
