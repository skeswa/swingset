# Checking a source interpretation before accepting it

Admission selects a checked interpretation as current. These rules define what a source must show before new evidence can replace earlier records.

[Overview](../recovery.md) · [Current status](../../status.md)

## 2. Page-kind contracts and admission

Give each supported page kind a versioned contract with the following fields.
Start with registry lookups, scoring event indexes, and round sheets. Other page
kinds remain explicitly unassessed until their contracts exist.

| Contract field            | Required meaning                                                                                                      |
| ------------------------- | --------------------------------------------------------------------------------------------------------------------- |
| Unit key and boundary     | Source-native scope and what one complete read includes; never a mutable canonical event ID.                          |
| Expected representation   | Recognized status and body shapes, including explicit empty and not-found forms.                                      |
| Interpretation accounting | Relevant fields and categories handled, deliberately excluded with reasons, or unknown.                               |
| Coverage witness          | Listed pages and children, totals and terminal pagination evidence where available; otherwise the precise limitation. |
| Guards                    | Named checks with severity, rationale, evidence, and reviewed policy version.                                         |
| Removal authority         | Which source-owned claims may be retired by complete enumeration, or `none`.                                          |
| Maintenance               | Owner, failure runbook, and reviewed changes to bounds or acknowledged source revisions.                              |

Interpretation is exhaustive within the declared scope: handle each relevant
category or field, deliberately exclude it, or emit an actionable failure.
Critical unknowns block the affected unit. Unexpected output must not
automatically lower a quality bound or update an acknowledged source hash. When
reliable extraction is unavailable, monitor a stable revision sentinel and open
review for unacknowledged changes; a reviewed local extraction can then be
updated with its supporting source. Revision discovery stays automatic even when
interpretation needs judgment. These mechanisms adapt zavod's
[interpretation](https://zavod.opensanctions.org/best_practices/strict_interpretation/),
[assertion](https://zavod.opensanctions.org/metadata/), and
[change detection](https://zavod.opensanctions.org/best_practices/change_detection/)
practices.

Extend the pure adapter result to declare interpretation accounting and coverage
evidence beside observations, child watches, and warnings: listed child URLs,
source-provided totals, pagination completion, supported fields, and known
omissions. The adapter performs no database writes. The evidence module
validates the declaration against archived inputs where independent checks
exist and stages an immutable source generation for the unit: unit key, ordered
input manifest and hashes, recipe and contract versions, observations, coverage
witness, and guard outcomes. An empty parse, a sudden loss of dates, or
`legitimate_empty=True` is not a coverage witness.

Completeness is scoped. Accepting an event index establishes its listed round
URLs without claiming those rounds are acquired or parsed. Judge-mark
expectations follow the round's role-specific panel definitions, not a universal
rectangular marks matrix. An unsupported source category or missing critical
date opens a specific interpretation gap instead of disappearing from coverage.

| Generation state     | Transition and effect                                                                                                          |
| -------------------- | ------------------------------------------------------------------------------------------------------------------------------ |
| `staged`             | Inputs and declarations are recorded; no current observations have been replaced.                                              |
| `waiting_for_inputs` | Required pages or intact artifacts are missing; retain the candidate and its acquisition requirement.                          |
| `needs_review`       | Critical unknown, inconsistent coverage, or a blocking guard failed; retain evidence and the prior accepted pointer.           |
| `accepted`           | Contract passed for the declared scope and current desired inputs; atomically select the generation and invalidate dependents. |
| `superseded`         | A newer desired input set or contract made this candidate obsolete; preserve it for audit without selecting it.                |
| `revoked`            | An accepted generation is proven inadmissible; remove its authority and invalidate its dependents.                             |

For mutable pagination without a source snapshot token, check available
revision markers and revalidate the listing after assembly. A changed listing
restarts the unit. Matching checks reduce risk but do not prove snapshot
isolation; if the contract cannot substantiate complete enumeration, record
that limit and grant no removal authority.

Admission is the parse writer's replacement transaction, extended: compare the
desired input fingerprint, select the accepted generation, replace its current
observation projection, and record downstream invalidation for both old and new
scopes. A stale worker cannot replace a newer generation or clear its work. A
crash leaves either the old state or the complete new state; staged inputs
remain available for retry. Staging happens before replacement, not as a guard
that runs after current observations have been overwritten. Failed or partial
units remain archived evidence but cannot claim complete enumeration.

Deletion by absence requires both an accepted complete enumeration and removal
authority in the contract for that scope. A failed fetch, missing page, run of
unused registry numbers, or registry not-found lookup supplies no such
authority; a not-found lookup establishes dated lookup absence only. An accepted
enumeration retires only absent claims owned by that source within its declared
removal scope. It does not delete archive evidence, another source's claims, a
person's existence or historical results, or review history. Corrections to a
present row can supersede that row's prior claims without granting whole-index
removal authority. An older accepted generation can remain usable with disclosed
age only while its links retain support; known wrong claims must still be
revoked.

Guard failures retain their input manifest and actionable reason. Reviewed guard
changes create a new contract version and rerun admission; they do not mutate a
failure into a success. Unknown fields declared noncritical generate warnings
without blocking unrelated accepted claims. An acknowledged manual-extraction
sentinel names the reviewed source revision and extraction; a new hash never
acknowledges itself.

This adapts the synchronization lesson from
[OpenAlex](https://help.openalex.org/access/sync/); WSDC supplies no snapshot
completeness or deletion interface. Admission precedes, and does not replace,
the compatible-generation checks at
[publication](publication.md#7-publication-coherent-evidence-safe-defaults-visible-degradation).
