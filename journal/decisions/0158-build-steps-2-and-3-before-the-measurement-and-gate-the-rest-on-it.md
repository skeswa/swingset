# D-0158: Build steps 2 and 3 before the measurement, and gate the rest on it

Status: Proposed  
Recorded: 2026-09-18  
Accepted: —  
Acceptance source: —  
Topic: State retention  
Supersedes: —  
Superseded by: —

## Decision

Plan steps 2 and 3 were written, tested and reviewed before step 1's measurement
was run. This record says so, says what that costs, and fixes what still waits
for the numbers.

Deployed nothing, so nothing was removed on the strength of a guess. Everything
that depends on the numbers stays shut:

- **Step 4 does not start** until the measurement has run and its receipt is
  retained. Its own gate is already closed in code: no payload byte can be
  removed until an `archived_generations` table exists.
- **The two limits are provisional.** 8,000,000,000 bytes and a window of 3 are
  judgment calls. The measurement is what decides whether they are the right
  numbers, and they are not deployed.
- **Interning identity or operating history is not started.** Plan section 6
  says the same trick applies to those tables if step 1 shows they matter. It
  has not, so nothing has been done to them.
- **The plan's step 1 was not done when this was written.** This record does
  not satisfy its "Done when", which needs a decision naming which later steps
  run and in what order _from the numbers_. The measurement ran later the same
  day and that decision is [D-0166](0166-hold-interning-back-until-rows-repeat-and-cut-indexes-first.md).

## Why

The plan's own order is measure, then intern, then walk. That order was not
followed, and pretending otherwise in the status page or in this log would be
the failure plan section 11 warns about.

The measurement needs a copy of a held production checkpoint. None exists on
this machine, and the held checkpoints live on the worker, which this work is
not allowed to touch. The tool to do it is written, tested against a built
state, and its operator commands are in the
[investigation](../investigations/2026/state-storage-measurement-2026-09-18.md);
what is missing is the data and an operator with access to it.

Given that, the choice was to build the two steps that are safe to build
without numbers, rather than to stop. Step 2 is reversible until its final drop
and changes no fingerprint. Step 3 removes only candidate directories a written
plan names, which the collector already removed on age, and it removes no row
data at all. Both are offline, tested, and undeployed. If the measurement shows
that output rows are not the biggest cost, step 2 is not wasted: it is the
migration that makes a recomputation cost what changed, and its saving is
bounded below by nothing. It would mean a further step for whatever is bigger,
which is what plan section 5 already says that decision must do.

Unverified until the measurement runs: whether output rows repeat between
generations often enough for step 2 to pay for itself, whether
`derivation_rows` was in fact the largest object, and whether either limit is
near the right value.

## Alternatives

- Wait for the measurement before writing anything. Rejected at the time: the
  data is not reachable from here, so waiting is indefinite, and the two steps
  that could be built safely would not have been.
- Write the "which steps, in what order" decision from the two whole-file
  figures in plan section 1. Rejected: those are the whole database and the
  whole checkpoint. They say nothing about which table is big, which is the one
  question step 1 exists to answer.
- Run the measurement against a small local database to produce a receipt.
  Rejected: a receipt from a state built by tests measures the tests. Retaining
  it under `journal/evidence/` would satisfy the plan's form and say nothing
  true about production.
- Deploy step 2 and measure afterwards. Rejected: that is the removal-first
  order the plan exists to avoid.

## Consequences

The plan's section 14 first clause is unmet, and stays unmet until an operator
runs the tool. The status page says so. The two limits carry a provisional
label wherever they are documented. Step 4 has one more precondition than the
plan gave it: the receipt, not just the two object stores.

When the measurement does run, its decision record supersedes this one on the
question of order, names what is actually biggest, and either confirms the two
limits or changes them.

## Links

- [Implementation plan, sections 5, 6 and 14](../../docs/plans/bounded-state-and-archive.md)
- [Measuring where state storage goes](../investigations/2026/state-storage-measurement-2026-09-18.md)
- [Bound state by interning payloads and one closure](0119-bound-state-by-interning-and-one-closure.md)
- [Measure state storage read-only on a copy](0127-measure-state-storage-read-only-on-a-copy.md)
- [Two retention limits and the collector age become policy](0141-two-retention-limits-and-the-collector-age-become-policy.md)
