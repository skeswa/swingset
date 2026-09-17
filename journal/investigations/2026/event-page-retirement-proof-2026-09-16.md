# Prove retirement of pages on one enumeration edge

Date: 2026-09-16 UTC  
Type: Investigation  
Topic: Implemented local immediate-edge retirement proof  
Related: [Fleet accounting](event-fleet-accounting-2026-09-16.md), [D-0034](../../decisions/0034-record-bounded-event-accounting.md)

## Summary

Verify one current enumeration and its immediate predecessor. Report a page as
explicitly retired only when an accepted authoritative replacement withdrew every
remaining claim for that normalized request. Do not infer retirement from a
smaller denominator, absent files, disabled watches, or missing queue work.

This was proposed after schema 22. No source or test files changed during
validation 009. After that validation passed, the coordinator authorized the
local schema-23 increment in [D-0035](../../decisions/0035-prove-immediate-event-page-retirements.md). Whole-event retirement and retirement history across
skipped enumeration versions remain unknown. There is no new job or history walk.

## Implemented local slice and review corrections

The local source now contains the pure declaration projection, one-edge proof,
current observations, immutable receipts, and nested `page_retirement` reporting.
`tests/test_event_retirement.py` covers admitted fixtures and integration. This
records implementation scope, not deployment or operating acceptance.

Independent review added strict serialized admission ordering: the replacement's
accepted decision ID must exceed both the predecessor creator's exact accepted
decision and each withdrawn claim's acceptance. Old but still usable support
cannot authorize a later withdrawal. Bind predecessor chronology to verified
metadata and its exact accepted decision; do not require unrelated predecessor
artifacts. Full artifact checks still apply to the replacement and affected claims.

Receipt identity excludes check times and observer policy/budget changes. Reports
allowlist fields and derive denominator assertions from the immutable proof, so
corrupt mutable observations cannot claim whole-event retirement or complete
history. A retirement-only visit preserves accounting history and page cursors.
Only a completed member pass starts or defers an edge; parent-priority early returns
also run the edge hook. A fresh intrinsically oversized edge yields after one
attempt instead of trapping page verification.

The remaining sections retain the design proposal, with these corrections taking
precedence where its initial wording was less precise.

## Exact retained authority

[`event_enumerations._record()`](../../../src/swingset/schedule/event_enumerations.py)
allows withdrawal only when the admitted generation has both
`removal_authority == 'watch'` and `report.proposed_removal == 'watch'`. It removes
old claims with the same `unit_key`, preserves independently owned claims, then
adds the accepted replacement's declarations. An absent request with an
independent surviving claim would violate that construction rule.

The replacement is the successor header's `generation_id`; the decision is its
`decision_id`. The generation's retained fields include `unit_key`, `page_kind`,
`contract_version`, `input_fingerprint`, `manifest_json`, `recipe_json`,
`result_json`, `report_json`, `previous_generation_id`, `work_token`, and
`removal_authority`. `recipe.context` identifies source, watch, and anchor snapshot.
`decode_generation()` verifies the fingerprint and content address, including
report/result and prior generation. It also checks that the removal column agrees
with the report. `previous_generation_id` is a source-unit predecessor, not the
source event's predecessor enumeration; do not confuse those chains.

[`Session.verify_operation()`](../../../src/swingset/admission/page_evidence.py)
already verifies an exact generation and accepted decision, cutoff, current
contract compatibility, explicit or equivalent-evidence revocation, manifest
snapshot identity, source context, and actual body/extract bytes. Its positive
support includes the generation content digest, exact accepted decision, policy,
and supporting snapshots. Reuse this path without repeating its authority logic.
A superseded generation can still support historical evidence; superseded is not
revoked. A current paused/shadow mode is not itself withdrawal of prior acceptance.
Keep the shared verifier's policy semantics and use the existing invalidation fence.

[`enumeration_evidence.members()`](../../../src/swingset/admission/enumeration_evidence.py)
checks each enumeration's content address and full bounded membership. That address
pins source/ref, predecessor, creating generation, parent support, members, and
pagination. It does **not** pin `decision_id` or auxiliary `removed_json`. Therefore
verify the exact accepted decision separately and recompute the difference. Never
use `removed_json` as proof.

## Minimal code changes

1. Extract the declaration projection from `_declarations()` into one pure shared
   function. Preserve its watch declarations, `file_row` obligations, direct-result
   identity, source/ref filtering, and parser-purpose rules. Supply its anchor
   watch/snapshot values explicitly. Enumeration bootstrap keeps its existing
   loader; the retirement verifier supplies those scalar values through
   `Session.read()`. Its current raw `SELECT * FROM watches` must not enter the
   bounded verification path. No adapter is rerun.
2. Add `admission/event_retirement.py`:

   ```python
   verify_edge(session, *, source, source_ref, successor_id,
               successor_members) -> dict
   ```

   The caller has content-verified `successor_members` in the same session and
   read snapshot. The function loads the exact predecessor once, verifies it with
   `members()`, and uses only shared session reads and exact-operation checks.
   It returns successor/predecessor IDs, `assessment`, `removed_request_ids`,
   `verified_retirement_ids`, reasons, and bounded proof identifiers. No writes.

3. Add a narrow persistence/report layer alongside `event_accounting`. Invoke it
   from the existing progress visit and fenced write transaction. Page availability
   and retirement are separate results: unfinished current pages do not prevent
   proof that a different old page was explicitly withdrawn.

The declaration extraction is important: duplicating its file/direct-result rules
would let enumeration creation and retirement proof disagree later. The only new
proof logic should be the bounded ownership/set comparison below.

## Required edge checks

After content-verifying both enumerations, require matching source/ref and the
successor's exact `predecessor_id`. Compute `removed = predecessor IDs - successor
IDs`. A missing predecessor is unassessed; a genuine initial enumeration has no
retirement edge. Empty `removed` means no removed pages on this edge, not that the
event is retired.

Load and decode the exact creating generation through the shared budget. Verify
its anchor operation against the header's exact accepted decision. Require watch
removal authority. Use the shared declaration projection to derive the replacement
requests for this source/ref.

For every removed request:

- Require at least one predecessor support claim. Every claim must belong to the
  replacement's unit. A single independently owned claim blocks retirement.
- Check each claim's `generation_id` against its decoded generation's actual
  `unit_key` and source. Verify its admitted artifact support through the same
  session and confirm that its admitted declarations supported this request/ref.
  Content-addressed enumeration metadata alone must not invent an old claim.
- Require the accepted replacement to omit the request. If it still declares the
  request, a successor omission is invalid, even when hashes are internally valid.

Memoize repeated claim-generation results only inside this session. Do not require
unrelated predecessor page bodies merely to prove retirement: the relevant old
parent declarations and the authoritative replacement are the evidence. A missing
old parent artifact, incompatible contract, revocation, malformed metadata, or
budget limit makes the affected proof unassessed. Conservatively return an
unassessed edge rather than a partial positive retirement total in the first slice.

Do not require source-unit `previous_generation_id` to equal the event predecessor's
creating generation; independent parents may have advanced the event in between.

## Persistence and bounded scheduling

Allocate the next migration only when implementation is authorized. Add:

- One mutable `event_retirement_observations` row per source/ref for the current
  edge: successor/predecessor, tri-state assessment, bounded verified-ID/proof JSON,
  reasons, checked/expiry times, full policy/fence token, and whether a fresh-budget
  retry is needed. It is a sampled proof, not authority to remove data.
- Immutable `event_retirement_receipts` per verified edge/proof digest, retaining
  the event, both enumerations, exact replacement generation/decision, verified
  request IDs, observation time, and policy/evidence identifiers. A unique key on
  event/edge/proof prevents repeated verification or restored files from recording
  another retirement. Index event history and all foreign-key child columns.

No existing data is retired by these tables. The source-owned withdrawal already
occurred during admission/enumeration construction; this records verified evidence
of that operation. Existing successful-progress receipts remain unchanged.

Use the existing session's row, JSON, artifact, and elapsed budgets. Each side has
at most 128 members; parent/claim candidates stay within the captured candidate
cap. Metadata and artifact work for retirement share the remaining batch allowance.
Record the exact new retirement-verifier format and caps in the policy receipt.
A policy change, restore epoch, revision, input bundle, or enumeration change
invalidates fresh retirement observations through the existing token.

Retirement must not starve behind repeated page scans. Attempt it after a completed
bounded page pass; if previous work exhausted the shared allowance, retain the edge
as needing a fresh-budget visit. On that event's next opportunity, prioritize one
edge attempt before restarting page verification. If this edge alone exhausts a
fresh allowance, retain explicit unknown and resume ordinary page work. Do not
prioritize that same oversized proof forever. Reuse the existing outer event
cursor/retry-subject behavior; do not add another queue or fetch loop. Empty
successors may receive an edge attempt without a page pass.

The H13 artifact-work boundary still applies. The observer fences every result
again at write time. Pause or a concurrent evidence change cannot create a
retirement receipt. Preserve receipts across backup/restore, while ordinary restore
activation invalidates fresh observations.

## Report contract and limits

Expose `retirement.scope='current_immediate_predecessor_edge'`, predecessor and
successor, assessment/reasons, verified request IDs/count when assessed, oldest
check and earliest expiry, and the latest immutable historical retirement receipt.
An invalidated current proof becomes unassessed; its historical receipt remains.

Keep `whole_event_retired=null` and `complete_retirement_history=false`. A verified
empty successor can say that all nonempty predecessor obligations were explicitly
retired. That says nothing about undiscovered pages, pagination, earlier skipped
edges, or whole-event retirement. The existing locally-accounted predicate still
requires nonempty current membership. This increment supplies no fleet lifetime
retirement stock, progress percentage, eligible clock, or operating acceptance.

## Meaningful acceptance cases

- Reuse the real admitted parent fixtures in `tests/test_event_enumerations.py`:
  authorized one-page withdrawal verifies; nonauthoritative omission preserves
  the denominator; another parent keeps a shared request alive.
- An event predecessor created by a different unit does not invalidate a lawful
  withdrawal from the replacement's own earlier claims.
- Forge `removed_json` or the unpinned decision ID: auxiliary data cannot produce
  a positive proof. Recomputed, internally consistent fake membership that omits
  a still-declared request must also fail.
- A support claim with a forged `unit_key`, unsupported request, or absent old
  parent evidence cannot become retirement. Missing current page bodies alone
  cannot become retirement either.
- Exact replacement or affected prior support revoked, contract changed, or
  body/extract missing/corrupt: current proof is unassessed, historical receipts
  survive. Ordinary supersession alone retains valid support.
- Shared declarations and repeated claim generations use one session and one
  normalized request identity. Oversized predecessors, claims, JSON, or artifacts
  yield unknown, with no per-edge budget reset.
- Repeated checks, crash/restart, restore, and repeated admission do not duplicate
  retirement. Changed fences or failed writes roll back the entire observer batch.
- A busy 33-page event and a costly first edge cannot starve later events or page
  checks. Test both exhausted leftover allowance and an intrinsically oversized
  proof with a fresh allowance.
- Skipped versions report only the immediate edge. Empty initial enumerations are
  not retired; a fully withdrawn predecessor still leaves whole-event retirement
  unknown. Query-only reporting performs no artifact reads or writes.

## Local validation and remaining work

Schema 23 implements the authorized declaration extraction, one-edge verifier,
current observation table, immutable withdrawal receipts, and nested reporting.
The focused run passed **139 tests**, covering retirement, enumeration, progress,
accounting, restore, migration, and foreign-key indexes. Ruff passed; mypy passed
for the six checked modules. Independent review passed all **31 retirement tests**
and found no remaining concrete blocker after the predecessor label was bound to
the immutable proof. These are local checks, not deployment or operating acceptance.

Combined [validation 011](../../evidence/runtime/event-completion-2026-09-16/event-recovery-validation-011.json)
passed all 2,014 tests in 420.71 seconds, Ruff, and mypy over 205 source files.
Source and test hashes stayed unchanged. The preceding retained run had one
stale unavailable-coverage test assertion; correcting it required no runtime
change. These receipts include the independently reviewed unavailable-origin
release coverage alongside schema-23 retirement.

Historical edge traversal, a complete lifetime retirement total, and whole-event
retirement remain unimplemented. Oversized, stale, invalidated, or unsupported
proofs remain unassessed. The observer supplies no eligible-time clock and does
not fabricate successful acquisition or interpretation from a withdrawal. No
frozen production source, live state, or production acceptance changed.
