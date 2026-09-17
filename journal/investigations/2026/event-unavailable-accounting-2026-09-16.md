# Account for observed unavailable page gaps

Date: 2026-09-16 UTC  
Type: Local implementation investigation  
Related: [D-0037](../../decisions/0037-account-for-supported-unavailable-page-gaps.md)

The release verifier already distinguishes a supported unavailable-origin
response from missing or unknown evidence. Schema 24 reuses that result in
local event accounting, under the same bounded progress session. This records
local work; it does not claim deployment or production acceptance.

## Implementation

`schedule/event_gaps.py` owns the three-state page-accounted rule and separate
gap observations. Exact response support, cutoff, verification policy, ordinary
invalidation token and TTL remain attached. A source-wide revision additionally
invalidates negative-domain observations when snapshot candidates change,
including undeclared aliases. It is deliberately separate from successful
progress qualification. Snapshot support rewrites and watch source changes also advance the ordinary
global epoch so cached positive alias support cannot survive metadata changes.

The progress observer captures the source revision once per source/read snapshot
and rechecks it in the observation transaction. A changed domain discards gap
results and records unknown accounting; it does not create a false reopening or
erase ordinary stage operations. Gap writes and other observations roll back
together on failure. Parent-priority assessment and final accounting use the
same interpreted-or-unavailable rule.

Reporting validates bounded stored metadata and its digest without opening
artifacts. It exposes unavailable and page-accounted subtotals beside unchanged
acquired/interpreted stage counts. The source event's live inventory independently
rechecks negative response bodies. Both require parent authority and a complete
bounded known enumeration; neither claims pagination or whole-event completeness.

Shared negative-support metadata validation checks false and unknown records too:
malformed stage booleans, request identity, or timestamps cannot become definite
unfinished work. The release validation path still checks exact current support
metadata and actual body bytes. All latest tied terminal responses must have
valid metadata before a stable support snapshot is selected.

## Focused validation

The final combined local run passed 167 tests in 13.44 seconds: new gap accounting,
existing accounting/progress/retirement, and unavailable release coverage. The
new file has 41 cases covering mixed 33-page rotation, parent verification,
unknown and malformed observations, body loss/restoration, alias candidates and
metadata rewrites, both terminal-tie orderings, concurrent gap invalidation,
atomic rollback, actual gap-to-success qualification, pause, expiry, restore
epoch, checkpoint preservation, and unchanged gap revisions for ordinary parse
bookkeeping, plus positive-alias watch source moves and no-op source updates. Independent review passed the earlier 39 cases. Checkpoint testing
proves low-level preservation; activation invalidation is covered separately by
the explicit epoch test. The five changed runtime modules passed Ruff and mypy;
coordinated final formatting passed. The full schema-24 run passed all 2,063
tests in 419.36 seconds, Ruff, and mypy over 206 source files, with source and
test hashes unchanged during validation. Its
[receipt](../../evidence/runtime/event-completion-2026-09-16/event-recovery-validation-012.json)
binds collection, test, and static-check logs. The full test log SHA256 is
`386a2e2e5bc03e958c501f04849f821deeca758196d81a73692c09f33fe00390`.

No source acquisition, deployment, or production state action was used for
this increment. Artifact-only loss remains a sampled-observation limitation,
not a permanent unavailable classification.
