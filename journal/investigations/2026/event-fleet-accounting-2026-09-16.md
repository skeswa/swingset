# Account for observed event completion without inventing a completion flag

Date: 2026-09-16 UTC  
Type: Investigation  
Topic: Proposed bounded fleet accounting  
Related plan: [History and recovery](../../../docs/plans/history-and-recovery.md), [event-completion extension](../../../docs/plans/recovery/README.md#event-completion-extension)

## Summary

Extend the existing request-progress observer with parent-support observations and
change-only event accounting receipts. Use its existing bounded verification
session, rotation, policy capture, and control boundary. Fleet reports can then
separate observed local completion, definite unfinished work, and unassessed
work. They must describe an observation window, not claim that every file remains
present at report time.

This began as a proposal. The first local increment below implements part of it;
remaining recommendations are not implementation or operating acceptance. It does
not change the frozen schema-14 production release. The inspected development tree
used schema 21; the read-only revision was
`ef2eef4ee01c15d30f933d3e4577777415114ebc`. The coordinator subsequently allocated schema 22 and D-0034 for the first increment.

## First local increment

The coordinator authorized a narrower implementation after this proposal:
[D-0034](../../decisions/0034-record-bounded-event-accounting.md). Schema 22 adds
parent observations and change-only accounting receipts. The code shares the
existing progress session and inventory parent check. The new focused suite is
`tests/test_event_accounting.py`; deployment and operating acceptance are not
claimed here.

Retirement proof, generic support roles, and proof payloads proposed below are
not implemented. Parent observations retain generation IDs, times, availability,
fences, and reasons. Reports expose the latest transition and last definite
assessment, with historical reopening totals explicitly unassessed. Only the
nested accounting API is paginated and bounded; the older outer doctor catalog
has its existing behavior. Recorded windows describe sampled availability, not
instantaneous file presence. Raw checkpoint preservation is tested separately
from epoch invalidation; only the normal restore activation verifies and
invalidates fresh hints.

The remaining sections retain the design considered before implementation.

## Existing seams

[Request progress](../../../src/swingset/schedule/event_progress.py) already rotates
at most eight events, verifies up to 32 selected members per event, and limits a
whole enumeration to 128 members. It shares one
[`Session`](../../../src/swingset/admission/page_evidence.py) across the batch.
The session bounds SQL rows, JSON, compressed and decoded artifact bytes, and
cooperative elapsed time. `verify_request()` establishes availability;
`verify_operation()` establishes support for one exact successful operation.
These are different claims and must remain separate.

[Enumeration verification](../../../src/swingset/admission/enumeration_evidence.py)
checks the enumeration content address, membership digest, and normalized request
identities. It does not verify parent artifacts. The
[inventory reader](../../../src/swingset/schedule/event_inventory.py) has the
needed parent check in `_Evidence.parent()`, but calling `inventory()` separately
for every event would create fresh budgets and repeat proof traversal.

[Schema 21](../../../src/swingset/state/migrations/0021_event_progress.sql) stores
per-request observations and immutable successful-operation/progress receipts.
The existing [report](../../../src/swingset/schedule/event_progress_report.py)
limits displayed observations to 20 by default. That display is not a denominator
for event completion. Accounting must inspect the complete bounded membership.

## Meanings and classification

Use `(source, source_ref)` as the event identity and the immutable enumeration ID
as the obligation version. Canonical mapping and publication remain independent.

| Term                          | Required evidence                                                                                                                                                                                                             |
| ----------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Observed locally accounted    | A nonempty, content-verified current enumeration; every required parent positively verified; every current member positively acquired and interpreted; all observations fresh under one compatible policy and current fences. |
| Observed unfinished           | At least one required current member or parent has a fresh definite negative. Other obligations may remain unassessed.                                                                                                        |
| Unassessed                    | No definite negative establishes unfinished work, but required observations are missing, partial, expired, malformed, or unknown. A legacy event without an admitted enumeration is unassessed.                               |
| Reopened                      | A prior locally-accounted observation followed by a definite missing or invalid obligation under the same enumeration. Record the cause and observation times.                                                                |
| Membership changed            | The enumeration changed. This is separate from successful work or reopening under an unchanged denominator.                                                                                                                   |
| Explicitly retired known page | An admitted authoritative replacement withdrew every remaining source-owned claim for that request, proven against predecessor and successor membership.                                                                      |

An initial positive observation is observed availability, not historical progress.
A smaller enumeration can be observed locally accounted, but its transition must
say `membership_changed`, not `successful_completion`. Restoring old files can
restore observed availability without creating an acquisition or interpretation
operation. Repeated verification creates neither successful progress nor another
identical accounting transition.

Unknown after a budget limit is not a reopening. Expiry removes an event from the
fresh accounted stock without asserting that work was lost. Preserve the last
definite historical assessment so an unknown interval does not duplicate a later
reopening. No sampled interval proves continuous eligibility or eligible age.

Pagination remains explicitly unknown under today's adapter contract. The
accounted claim concerns known listed obligations, not all pages that might exist.
An initially empty enumeration is neither complete nor explicitly retired.

## Proposed persistence

Add two tables in the next allocated migration; do not add a mutable `complete`
column or a second event catalog.

1. `event_accounting_support_observations`: current bounded support facts keyed by
   `(source, source_ref, enumeration_id, role, support_key)`. Roles are `parent`
   and `removal`. Store `availability` as true/false/null, `observed_at`,
   `valid_until`, `token_json`, `reasons_json`, and bounded `proof_json` identifying
   the generation, decision, and predecessor where applicable. These are
   replaceable observations, not authority. Remove obsolete-version observations
   when visiting a new enumeration; durable history lives in receipts.
2. `event_accounting_receipts`: append-only, change-only assessments. Store an
   integer receipt ID, event identity, enumeration ID, previous receipt ID,
   transition kind, assessment, `observed_at`, earliest contributing check,
   expiry, token/policy, and bounded evidence summary. The summary includes
   required/positive/negative/unknown member and parent counts, plus verified
   removed request IDs for a retirement transition. Do not embed HTML or manifests.

Index both tables by event identity. Index every foreign-key child column,
including enumeration and predecessor receipt references. Retain proof identifiers
as text where target deletion must remain diagnosable, following operation
receipts. Add immutable update/delete guards to receipts. No historical success
or completion timestamps are backfilled during migration.

Use the existing progress policy table, with a new policy format covering parent
and removal verification and their caps. Reuse the current enumeration/revision/
epoch/input-bundle/policy token, with exact key validation. Accounting receipts
are historical records; their presence never authorizes scheduling or promotion.
A fleet report derives its fresh classification from current observations and
membership, rather than treating the latest receipt as a live completion flag.

## Integration and interfaces

Extract `_Evidence.parent()` into a neutral admission helper:

```python
parent_support(session, *, source, generation_id, decision_id=None) -> dict
```

It uses the supplied session for all metadata and exact-operation verification.
The inventory reader delegates to it. There is no schedule-to-build import and no
second implementation of artifact traversal. Allow an exact decision for removal
proof; otherwise preserve current inventory behavior.

Add a narrow `schedule/event_accounting.py` module with these responsibilities:

```python
observe(session, *, source, source_ref, enumeration_id, members,
        token, now, prior_observations) -> dict
persist(conn, observation, *, now) -> dict
report(conn, config, *, now, source=None, source_ref=None,
       after_event=None, limit=100) -> dict
```

`observe()` receives already-verified membership from progress and reuses fresh
request observations, including results from the current batch. It verifies
missing or stale parents and, when applicable, one immediate predecessor removal
edge. It must not call `verify_request()` again for pages just assessed by progress.
`persist()` runs inside the existing progress write transaction after a fresh
fence check; changed fences discard the entire accounting result. A failed write
rolls back request and accounting observations together.

`report()` is read-only and reads no artifacts. Bound each catalog page and each
event's metadata. Return a continuation cursor and `coverage_complete=false`
when the catalog page is truncated; do not present subtotal counts as fleet
totals. A caller can aggregate catalog pages under one consistent read snapshot.
The existing explicit-event doctor path still provides a fresh artifact check.

Extend the existing `event_progress.refresh()` call rather than adding another
cycle worker. Its H13 source/archive/parse/admission boundary remains in place.
Paused verification produces no new observation. Keep blocker bookkeeping's
separate pause behavior unchanged. The event report nests accounting alongside
operation progress and acknowledged publication; it does not replace either.

## Budgets, freshness, and fair progress

Keep the current proposed, unmeasured limits initially: eight events, 32 selected
pages, 128 total members per enumeration, and the shared `Limits()` budget. Its
defaults include 1,024 rows, 8 MiB aggregate JSON, 8 MiB compressed bytes, 32 MiB
decoded bytes, and two cooperative seconds. The caller's remaining wall allowance
may lower the effective seconds; capture that exact effective policy. Bound parent
sets by the existing candidate cap, initially 64. These are safety bounds, not
measured throughput targets.

All parent, predecessor, member, and receipt metadata consumes the same session
budget. No per-page, per-event, or removal-proof reset is permitted. A SQL or
filesystem operation is not preempted by the cooperative time limit.

Avoid starving parent checks behind repeated page checks: when all current member
observations are already fresh and positive, use the next event visit to verify
parents before repeating page verification. If a required parent set itself cannot
fit the budget, leave the event unassessed. Do not add a second traversal framework
or silently increase limits to force a positive answer.

Preserve current cursor rules: stop after global exhaustion; advance only through
work actually attempted. A later member that receives an already-depleted budget
gets a fresh opportunity on the next visit. A single oversized obligation may be
recorded unknown and advanced so it cannot trap the whole catalog.

Expiry is the earliest contributing observation expiry, never the latest summary
write time. Missing, malformed, or mismatched token/policy makes that observation
unassessed. Database invalidation, accepted input changes, revocation, and restore
invalidate fresh stock through existing fences. Unrecorded filesystem loss can
remain undetected until verification; report the oldest check and this limitation
explicitly. Live doctor verification can detect it immediately.

## Retirement authority and the first-slice limit

[Enumeration construction](../../../src/swingset/schedule/event_enumerations.py)
withdraws claims only when the admitted generation has `removal_authority='watch'`
and its report proposes watch removal. It removes claims owned by that unit;
independent support survives. Preserve this exact authority.

The observer must content-verify predecessor and successor, recompute removed
request IDs, verify the exact admitted replacement and its artifact support, and
check that each removed request had no surviving independent claim. Do not trust
`removed_json`: that auxiliary column is not covered by the enumeration content
hash. Likewise, a header decision ID must be checked against actual accepted
support, not trusted merely because it is in the row. Use bounded reads of the
verified generation/report; do not reconstruct adapter outputs a second time.

For the first slice, verify only the current enumeration's immediate predecessor
edge. Record qualified retirements observed there. A jump over unobserved versions
leaves retirement history incomplete. Older receipts establish historical observed
retirements, not a fresh count of every currently retired obligation. Report that
count as unknown until a separate bounded history traversal is designed and tested.
This limit keeps the first implementation small and avoids silently walking an
unbounded predecessor chain.

There is no existing whole-event retirement authority. Name the available metric
`events_with_observed_explicit_page_retirements`; do not call the event retired.
An empty successor after a verified removal can report that its predecessor's
known obligations were retired, with pagination still unknown. Disabling a source,
sealing a watch, losing a file, rejecting a map, or deleting queue hints is not
retirement. A revoked retirement proof becomes unassessed currently; keep its
historical observation and explain the invalidation.

## Report contract

Return schema support, scope, catalog cursor, captured policy, observation bounds,
and separate counts for fresh locally-accounted, definite unfinished, unassessed,
and legacy-unenumerated events. The first three are mutually exclusive within the
assessed catalog; legacy-unenumerated is a subset of unassessed. Report historical
reopening and retirement transitions separately from current stock. Include
membership changes and partial-observation reasons without calling them repairs.

For an event, expose the current enumeration, listed denominator, acquired and
interpreted positive/negative/unknown counts, parent counts, oldest check, expiry,
and latest definite historical accounting receipt. A fresh definite negative may
establish unfinished status even while totals are partial; mark that distinction.
Always expose pagination status, incomplete historical coverage, and
`eligible_service_age_seconds=null`. No percentage or ETA follows from an unknown
universe, blocked cohort, or partial fleet page.

## Acceptance cases

| Scenario                                                                 | Required result                                                                                                |
| ------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------- |
| 33 listed pages, only 32 verified                                        | No positive event accounting until the remaining page and every parent are verified.                           |
| Initial retained positives                                               | Availability can be accounted; no historical successful-progress timestamp is invented.                        |
| Same-enumeration page or parent loss, corruption, or revocation          | Fresh definite negative reopens a previously accounted event once; unrelated valid facts remain visible.       |
| Budget exhaustion, expired observations, or malformed policy             | Unassessed rather than reopened or complete; no crash and no budget reset.                                     |
| Old files restored versus actual reacquisition                           | Both can restore availability; only the independently qualified operation can count as progress.               |
| Shared request or aggregate manifest anchored on another watch           | Same shared verifier result; count the normalized request once per event.                                      |
| Membership shrink or growth                                              | Membership-change receipt; no successful progress from denominator changes.                                    |
| Authoritative omission, unauthorized omission, and shared parent support | Only fully withdrawn, verified source-owned claims count as explicitly retired.                                |
| Forged removal metadata or skipped predecessor versions                  | No fabricated retirement; incomplete history remains explicit.                                                 |
| Empty initial enumeration or more than 128 members                       | Unassessed, not vacuous completion; metadata reads remain bounded.                                             |
| Expensive first event and many continuing arrivals                       | Later events and partially attempted pages receive fresh-budget opportunities; parents are not starved.        |
| Concurrent enum/input/revision change before commit                      | Discard observations atomically and preserve prior historical receipts.                                        |
| Repeated unchanged checks, failure loops, or restart                     | No duplicate successful-progress or accounting transitions.                                                    |
| H13 pause, query-only doctor, checkpoint/restore                         | Pauses block artifact work; report never writes; restore preserves history but invalidates fresh availability. |
| Canonical mapping missing or publication absent                          | Local accounting remains independent; publication stays separately unacknowledged.                             |

## Conclusion and follow-up

The first increment is implemented in schema 22: shared parent verification,
bounded sampled classifications, change-only historical transitions, and a
paginated metadata report. Retirement and historical transition totals remain
explicitly unassessed. Independent review found and resolved timestamp ordering,
overlong expiry, and parent-priority edge cases.

Full validation passed at 20:50:21 UTC: 1,957 tests in 405.52 seconds, Ruff, and
mypy over 201 source files. Source and test hashes remained unchanged during the
run. See [validation 009](../../evidence/runtime/event-completion-2026-09-16/event-recovery-validation-009.json).
This is local implementation evidence; the frozen schema-14 production release
does not contain these changes.

Authorize parent observations and bounded fleet classification first, then the
immediate-edge retirement proof under the same session. Require focused tests and
a source-bound full suite before any deployment proposal. Full retirement stock
across skipped historical versions, enumerations beyond the current proof bound,
eligible-time alarms, measured service objectives, and operating acceptance remain
separate work. This investigation does not close V6.
