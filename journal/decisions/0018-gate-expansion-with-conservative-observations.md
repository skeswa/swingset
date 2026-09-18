# D-0018: Gate new event indexes with conservative local observations

Recorded: 2026-09-16  
Decided by: agent  
Topic: Scheduling  
Supersedes: —  
Superseded by: —

## Decision

Implement the first local slice of [D-0015](0015-bound-event-expansion-observations.md)
in an isolated change. Track source events that have begun event-specific work,
retain event evidence, or have admitted result obligations. Mere discovery of
an unfetched event index does not enroll it as unfinished work.

Use compact, versioned observations of known-page acquisition and interpretation
to guide expansion. Observations do not authorize stage completion. Partial,
stale, invalidated, unsupported, or oversized checks retain pressure. Mapping,
identity, and publication gaps stay outside this acquisition gate.

For each request host, defer starting additional event indexes above 100
unresolved or unknown started events; reopen below 80. Preserve the previous
decision between those thresholds. These are proposed, unmeasured defaults.
Keep essential discovery and started-event continuations under their existing
gates, and preserve the listed-result share. Borrowing cannot reopen deferred
new-event admission.

## Verification boundary

Refresh at most eight events in one bounded cycle operation, with at most 32
request members per probe. Bound returned database values, candidate rows,
generation JSON, manifest members, compressed and decoded artifact bytes, and
cooperative elapsed time. No artifact recovery or source request occurs.

Advance past an oversized page with an explicit unknown result so later pages
can be checked. An oversized parent remains unknown without suppressing bounded
child checks. Only a fully traversed enumeration with valid parents and no
missing or unknown required pages can reduce local pressure. Empty or missing
enumerations remain unresolved.

Fence every batch and final observation with its enumeration, subject revision,
global invalidation epoch, accepted input bundle, and verification policy. Its
maximum age starts at the earliest contributing check, initially 24 hours.
Rotate verification across subjects. Queue and policy state survive restart;
neither attempts nor a changed policy refund issued service.

Track shared parent watches separately from result membership so a new parent
admission invalidates dependent observations even across capture units. On
verified restore activation, recover abandoned execution admissions under the
exclusive restore locks, preserving publication uncertainty, then increment
the observation epoch before removing `RESTORE_PENDING`. An interrupted
activation can repeat that invalidation safely. Retained checkpoints are not
modified, and issued request accounting is preserved.

## Limits and integration

An oversized but fully retained event can keep pressure indefinitely until its
verification support improves. Report this limitation; do not treat budget
exhaustion as absence or success. SQL and filesystem operations do not have a
hard deadline. Corruption outside SQLite can occur within the observation
window; live diagnostics continue to verify current bytes.

Retain historical request-host associations conservatively. Moving an event's
URL can leave unresolved pressure on the old host; retiring these associations
needs separate evidence. This can defer expansion longer than necessary.

Selection, delay calculation, and the pre-debit gate use the same expansion
decision. Record the actual start only with an issued request. Refresh uses the
existing controlled local-operation boundary; diagnostics remain read-only.
This change does not modify the frozen original H16 source or clear production
holds. Full local tests and later retained-demand and operating acceptance are
still required.
