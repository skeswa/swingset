# Recording gaps and the work they need

A requirement records a rule that the current evidence or output does not satisfy. It also explains the next action or why work is blocked.

[Overview](../recovery.md) · [Current status](../../status.md)

## 3. Requirements: one inventory for gaps, findings, and derivation work

Add a reconciliation module with one main interface: given a consistent local
view, policy, and time, compute unmet requirements and justified next actions.
Persist the differences transactionally. Source adapters declare supported
discovery and interpretation capabilities; the reconciler does not invent URLs
or resolve ambiguous identities itself.

Start with the requirement kinds already measured:

| Requirement kind                                      | How it is discovered                                                        | Example next action                                                                  |
| ----------------------------------------------------- | --------------------------------------------------------------------------- | ------------------------------------------------------------------------------------ |
| Listed round has usable observations                  | Supported event page lists a round URL.                                     | Fetch, restore, or reparse that round.                                               |
| Source event has sufficient mapping evidence          | Accepted index or event observation lacks a unique event mapping.           | Read a known metadata page; otherwise request parser or alias review.                |
| Source-asserted WSDC ID has been checked              | An entry carries an ID absent from verified registry state.                 | Direct lookup of that ID within the host budget, even beyond the discovery frontier. |
| Recent first-point finalist can be reconsidered       | Eligible results have an unresolved person or registry relationship.        | Probe new IDs and relink when new dancers arrive.                                    |
| Registry occurrence has a supported event association | Registry provides a series and month.                                       | Search known local indexes, supported historical discovery, or mapping review.       |
| Materialized scope uses current inputs                | Desired and materialized fingerprints differ.                               | Recompute the scope and affected dependents.                                         |
| Published link has sufficient support                 | A contradiction, expiry, suppression, or policy change invalidates support. | Revoke or recompute before publication.                                              |
| Required archive artifact is usable                   | Parse, build, or periodic integrity check finds missing or corrupt bytes.   | Restore by digest; refetch only if the source representation is replaceable.         |

An unresolved first-point result stays eligible after the intensive
confirmation window. These repairs use existing host budgets and never
establish a registry completeness percentage over an unbounded namespace.

### Stored requirements

Use stable keys `(requirement_kind, subject_key, policy_version)` and record
desired input fingerprint, state, first detected time, last progress time,
attempt count, next eligible time, supporting evidence, and blocking reason.
Aggregate shared gaps: one unmapped registry occurrence can explain thousands
of placement joins and is one requirement, not thousands of acquisition
requests.

States are `ready`, `retry_wait`, `waiting_for_source`, `needs_implementation`,
`needs_review`, `unavailable`, `out_of_scope`, and `satisfied`. An active worker
is attempt state, not requirement state. Input or policy changes can reopen a
satisfied requirement.

The `findings` table becomes this inventory. Each existing finding kind is a
requirement kind whose next action is review, in state `needs_review`. The
existing owner replacement rules in
[architecture](../architecture.md#findings-and-review) are the postcondition check
for those kinds: a parser finding closes when the watch's observation set no
longer produces it. Unknown problem kinds stay visible for triage. Code
changes and uncertain aliases still need engineering or review; retrying cannot
supply missing interpretation logic.

The stored inventory is rebuildable from evidence, accepted policy, and the
decision journal. Attempt history is the only part that is not derivable. A
bounded periodic scan with a durable cursor and lag measurement recomputes
every stored kind over retained history, not only recent partitions, so a lost
row is recreated on the next scan. Existing orchestration defaults can be
narrower; see
[Dagster's documented condition behavior](https://docs.dagster.io/guides/automate/declarative-automation).

### Derivation work is a query

For map, project, link, and build, no requirement row is stored. Every
materialized scope records its input fingerprint and recipe. The desired
fingerprint is computed from current upstream revisions, captured inputs, and
the current recipe, using the dependency table that `state/work.py` already
owns. A scope is pending exactly when the two differ. Committing upstream output
changes downstream desired fingerprints in the same transaction, so a lost
enqueue cannot happen and no scan repairs it. The pending work tables for those
stages are replaced by this comparison; a unit still commits its output, its
materialized fingerprint, and its revision bumps together.

If desired inputs change during an attempt, the attempt's materialized
fingerprint does not match and the scope stays pending. An old completion
cannot satisfy the new requirement or clear newer work. Work completion is
recorded against the input generation it actually processed.

Offline selection may reuse exact currentness answers within one owned read-only
snapshot. The bounded cache includes the work unit and context, expires when that
snapshot closes, and never becomes stored authority. Mutable caller transactions,
inline derivation groups and filesystem-dependent build checks stay uncached.
Selection preserves complete scope discovery and candidate order; normal worker
admission rechecks controls and dependencies after the read snapshot closes. See
[D-0072](../../../journal/decisions/0072-profile-offline-selection-before-changing-replay.md)
for the measured duplicate work and validation.

The same owned snapshot may reuse a complete dancer-cohort readiness result,
keyed by the exact ordered cohort and ordinary database currentness callback.
Custom callbacks and caller-owned transactions always recompute. This result
uses the same bounded cache and freshness guards; normal worker admission still
rechecks current controls and dependencies after selection.
