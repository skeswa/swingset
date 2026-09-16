# Measuring release completion time (H16)

> Historical record. Dates, status claims, and instructions below describe the
> original work. See [current status](../../../docs/status.md) before acting.
> [Project journal](../../README.md)

A release needs saved evidence for every selected result. This record concerns checking that evidence, completing a build, or preparing deployment. The original work ID is H16.

The 093 full scratch build wrote candidate `cand_d80b9025652f4e10`, including
its changelog, within the memory limit. Maximum process RSS was 5,562,844 KiB;
the monitor did not terminate it. Completion then exceeded the unchanged
45-second write deadline and rolled back. The candidate's files are retained,
but it has no committed build-generation receipt and is not accepted for release.

The journal identifies closure validation during completion. One read-only
validation of the actual 34,986-generation graph took 25.59 seconds. Removing
repeated whole-set copying and duplicate generation reads reduced it to 11.80
seconds. The whole completion still timed out: the wrapper and materializer
performed four full validations inside one transaction.

The final narrow change:

- Accumulates traversed dependency sets once and keeps continuity witnesses
  separate until the final inventory union.
- Compares each selected receipt with the complete record already read by graph
  traversal, preserving every field and cycle/dependency check.
- Uses one acceptance query instead of three; receipt-only validation does not
  fetch parse results. Support retains compact receipts instead of full results.
- Removes the wrapper's redundant validation. The materializer still validates
  before writing output, after writing rows, and before final certification.

No cache, deadline increase, schema change, source-policy change, or shortened
runtime recipe was introduced. Retention and completion still share rollback.
Tests cover query equivalence, continuity traversal, forged receipts, exact
validation order/count, and rejection/rollback after revocation, policy or
snapshot-body changes. The final focused suite passed 51 cases; the tightened
four completion cases passed again after independent review.

An independent diagnostic copy of the failed build state completed successfully
in 38.82 seconds with all three validations and the ordinary 45-second bound.
This used explicitly recorded module overrides solely for diagnosis. It is not
a release acceptance receipt and cannot replace a new frozen replay and build.

Full frozen tests, replay, build, substantive audit, deployment and production
release remain required under the new runtime identity. Production is unchanged.
The separate pending Monterey parser/alias edits and the newly planned event
completion extension remain outside this original H16 release scope.

Evidence is under `verification/h16-changelog-build-*20260914*` and
`verification/h16-closure-*20260914*`.

## Full frozen build result, 2026-09-15 UTC

The 2d5 frozen source passed all 1,199 tests and its full 34,986-scope replay.
Independent replay verification passed. The subsequent full build still
exceeded the 45-second completion deadline in the final `_certify` validation,
while reconstructing closure support. Its transaction rolled back after
387.66 seconds total process time. Maximum RSS was 4,831,260 KiB; no memory
stop occurred. The isolated diagnostic's 38.82-second completion did not
provide sufficient margin under full-build conditions.

The failed state and candidate files remain at
`/var/tmp/swingset-h16-closure-build`. They are not accepted for publication.
See `verification/h16-closure-build-20260915.json` and its resource/journal
receipts. The owner paused production steps after this build; no new deployment,
production initialization, candidate acceptance audit or publication followed.

The [2026-09-15 validation investigation](h16-validation-investigation-2026-09-15.md)
adds fresh read-only timings and a profile of this exact failed-build state.
Three validations passed in 34.78 seconds combined; this excludes the rest of
completion and does not resolve the full-build deadline failure.
