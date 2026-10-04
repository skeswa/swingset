# D-0249: A waiting failure with the same message is a repeat

Recorded: 2026-10-12  
Decided by: owner, 2026-10-12; source: approval of the maintenance item 17 fixes, relayed to the implementing session  
Topic: Scheduling  
Supersedes: the "new input fingerprint makes it fresh" part of D-0216's waiting repeat  
Superseded by: —

## Decision

A unit that waits for input with a recorded error (`parse_failed`,
`unsupported_page_kind`, or `artifact_unavailable`, whose message names the
missing artifact) and fails again with the same error class and the
same message is a waiting repeat, even when its input fingerprint changed. A
repeat is not a failure of the derive tick and does not count in the
per-source 10% rule. A new message, or no earlier message to compare, is
fresh. The wait clock does not change: a new fingerprint still restarts it at
24 hours. A blocked parse now keeps its extract or parse error in the attempt
evidence and the queue row, so the next failure has a message to compare.

## Why

Any deploy that touches a kind's code changes its units' fingerprints. The 11
known parse failures then counted as fresh after each deploy and failed small
derive ticks: 12 of 13 failed ticks from 2026-10-04 to 10-12. The page and
the error were the same each time.

## Alternatives and consequences

Quarantining the 11 units would hide them from retries that a fix needs. The
cost of this rule: a fix that leaves the same message gives no new failure
signal; the queue row and `parse_failure` finding still show it.

## Links

- [State: work queue](../../docs/reference/state.md#work-queue)
