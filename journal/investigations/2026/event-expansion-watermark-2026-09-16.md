# Bounding event-index expansion without hiding unfinished work

Date: 2026-09-16  
Type: Investigation  
Topic: Scheduling  
Related plan or decision: [History and recovery](../../../docs/plans/history-and-recovery.md), [D-0015](../../decisions/0015-bound-event-expansion-observations.md)

## Summary

Controlled index expansion needs bounded verification work outside request
selection. Compact observations can guide scheduling, but cannot establish event
completion or replace live evidence checks. This design is **not implemented**
and has no operating acceptance. It prepares the next code increment; it does
not complete the event-completion extension.

## Context

The schema-18 working tree retains admitted event page obligations, rotates event
turns, and protects listed-result capacity. It does not yet limit admission of
additional events when local acquisition or interpretation falls behind.

The [retained watch demand](../../evidence/runtime/event-completion-2026-09-16/watch-demand.json)
contains 6,318 watches and 468 source references. These are raw inventory counts,
not verified unfinished events, eligible requests, or evidence for thresholds.
No new operating measurement, network request, or VM action was performed for
this investigation.

## Findings

### Counting every listed event would prevent work from starting

A platform index can list many event indexes before any event-specific request
has run. Treating all those unknown events as unfinished pressure could close
admission before the first event can start.

Count an event as started after an actual event-specific request, retained
event-specific evidence, or admitted result obligations. Merely discovering an
unfetched event index does not start it. Apply the admission gate to entering
additional events; essential platform discovery and required continuations of
started events retain their existing eligibility checks.

Track pressure per request host and source event, independent of canonical
mapping. Shared requests retain each event association. Request accounting still
charges the actual issued host once.

### Current verification is not bounded by a page count

[Event inventory](../../../src/swingset/schedule/event_inventory.py) checks a whole
event. One page can traverse many snapshots and generations; one generation can
reference many artifacts. Calling it once per event does not provide a bounded
refresh worker.

[Archive reads](../../../src/swingset/fetch/archive.py) load whole files. Body
reads decompress the entire gzip; extract reads parse the entire JSON. The
response ceiling in [request contexts](../../../src/swingset/fetch/limits.py)
does not bound all retained historical artifacts or a generation's manifest.
A wall-time check between pages cannot bound that work either.

Subsequent work adds [streaming body verification](../../decisions/0016-stream-event-body-verification.md)
for inventory's digest-only check. This bounds its read buffer but does not cap
total artifact bytes, time, or generation history; the observer requirements
below remain open. The parser-facing body reader still returns the whole body.

### Scheduling observations must remain conservative hints

Pressure concerns local acquisition and interpretation. Mapping and publication
gaps must not force another download. Unknown pagination must stay visible;
accounting for known pages does not establish a whole-event denominator.

Missing, dirty, stale, corrupt, revoked, unsupported, and partly checked evidence
must not lower pressure. Pausing a watch or waiting for retry does not erase its
obligation. Live inventory remains the authority for the bytes present when a
reader asks. An observation's maximum age explicitly limits how long scheduling
may rely on an earlier check; out-of-band file damage can occur inside that
window.

## Recommended implementation boundary

Add a narrow `event_pressure` module with bounded `bootstrap` and `refresh`
operations, transactional `record_started` and invalidation operations, and an
`admission` query that reads SQL only. Keep existing host/class ranking and all
fetch gates. Apply admission filtering consistently to selection and delay
calculation, before protected-capacity selection.

Use compact current records, with no persisted completion flag:

| Proposed record             | Purpose                                                                       |
| --------------------------- | ----------------------------------------------------------------------------- |
| Subject keyed by source/ref | Start basis, input revision, partial scan cursor/counters, latest observation |
| Host/source/ref association | Indexed host exposure without canonical-map dependence                        |
| Host latch                  | Deferred/open state, captured policy digest, transition reason                |
| Global epoch                | Rare policy, revocation, or restore invalidation                              |

An observation records its enumeration, subject revision, global epoch, policy
digest, parent validity, checked and missing stage counts, blockers, earliest
verification time, and expiry. Keep only the current scan and observation; avoid
history that grows on every scheduler call. Aggregate compact subjects through
the host index rather than maintaining a second mutable count cache.

Resume verification across parents, generation manifests, request members,
snapshot/generation candidates, and artifacts using stable cursor identifiers.
Bound candidate rows, manifest members, artifact bytes, pages, events, and time.
A bounded local streaming digest reader must stop oversized artifacts as
`verification_budget_unknown`; it must not attempt network recovery. Elapsed
time remains cooperative between bounded reads, not a filesystem deadline.

Partial scans cannot reduce pressure. Rotate subjects so a large event does not
monopolize refresh. Restart a scan when its input revision changes. Publish its
observation only after all required checks finish, and derive expiry from the
earliest contributing check. An expired scan never becomes fresh simply because
its last page finished now.

Invalidate a subject when its enumeration changes or an indexed supporting watch
receives relevant snapshots or admitted interpretation. Include its event-index
watch. Rare revocation, admission-policy, and restore changes can invalidate all
hints through the epoch. Failed attempts, pauses, alias changes, mapping, and
publication must not erase acquired evidence or paid service.

For each host, combine observed local gaps with unknown subjects conservatively.
Close new-event admission above a high threshold; reopen below a lower threshold;
preserve the latch between them and across restart. Capture the policy used.
Borrowing can serve eligible essential discovery and continuations when listed
work is blocked. It must not bypass closed admission for additional events.

## Conclusion and follow-up

Implement the bounded verifier and invalidation seam before adding the watermark.
Choose thresholds, observation age, and refresh budgets through explicit proposed
configuration and later measurement. This investigation supplies no calibrated
defaults. Whether a reviewed permanent gap can leave pressure also remains open;
until defined, keep it unresolved with its reason visible.

Fake-clock acceptance should cover initial admission, continuous arrivals,
hysteresis/restart, host exposure, borrowing, pauses/retries, policy reload,
mid-scan invalidation, large-event checkpoint progress, expired scans, corruption
and revocation. Confirm mapping/publication gaps do not change pressure and that
no artifact read occurs in selection or request accounting.

## Evidence and reproduction

Review the linked archive and event-inventory implementations alongside
[protected capacity](../../../src/swingset/schedule/event_capacity.py) and the
[shared parser purposes](../../../src/swingset/schedule/event_request_kind.py).
The conclusions are a static review of the schema-18 working tree on this date,
not runtime verification. The retained demand file provides context only.
