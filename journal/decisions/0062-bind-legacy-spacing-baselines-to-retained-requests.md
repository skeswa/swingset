# D-0062: Bind legacy spacing baselines to retained requests

Recorded: 2026-09-17  
Decided by: agent  
Topic: Schema-28 spacing adoption  
Supersedes: —  
Superseded by: —

## Decision

Prepare a read-only per-host proposal before applying any legacy request-spacing
baseline. Pin the helper, reviewed runtime, effective configuration, hold, paid
usage, controls and retained request evidence. Apply only the exact coordinator-
reviewed proposal under writer and control locks at schema 28. Preparation may
read schema 14; application must not migrate it.

The first supported proof covers the eight-request Archive fixture operation on
2026-09-17. Its exact receipt, request admissions, original H16 host policy,
reviewed runner, retained robots response and bodies establish a ten-second
original effective gap. A later paid request or differing admission invalidates
that proposal. A second proof explicitly combines that operation with the exact
two-request DCN index fixture: ten paid requests and 2,934,701 bytes. It verifies
both bundles, the predecessor ledger, all ten admissions, and the later retained
robots response and completion-based ten-second gap. Any subsequent request
invalidates this second proof too. Other paid hosts remain blocked without their
own reviewed proof.

## Why

Old grant timestamps and today's configuration cannot reconstruct a prior robots
delay or prove completion. The fixture retained an exact original policy and
robots response, making a narrow operational baseline reviewable. Its robots
fetch did not update the production robots cache, so that proof must use the
retained fixture response instead of treating the cache as its current support.

An inactive writer, unchanged operator hold and no unsettled admissions are
required. Application records an immutable evidence reference through the
existing `Gate.establish_spacing_baseline` API. A write authorizer forbids budget
and control mutations, and protected-table checks reject other changes. The
existing host deadline remains exact; the next ordinary acquisition must still
wait a fresh full gap. Reapplying the same untouched proposal creates no new
baseline or reset.

## Consequences

This records reviewed operational evidence; it does not grant a new source,
accept a year, activate collection or certify historical spacing compliance.
The original fixture's two short recorded intervals remain findings. A later
acquisition requires new exact evidence before either Archive candidate can be
applied. No production baseline is established by preparing this helper.

## Links

- [Spacing decision](0056-anchor-host-spacing-to-request-completion.md)
- [Helper and validation](../investigations/2026/legacy-spacing-baseline-preparation-2026-09-17.md)
