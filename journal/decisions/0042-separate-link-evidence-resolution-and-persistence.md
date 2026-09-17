# D-0042: Separate link evidence, resolution, and persistence

Status: Accepted  
Recorded: 2026-09-17  
Accepted: 2026-09-17, project owner  
Acceptance source: Owner request, “Please refactor in this style,” following the evidence → resolution → persistence proposal in this session.  
Topic: Identity linking  
Supersedes: —  
Superseded by: —

## Decision

Keep `link_event` as the public operation. Load an event's retained evidence
and selected input versions, resolve identities without storage access, then
commit the result atomically against the same input versions.

Represent subject evidence, candidate assessments, and event resolutions
explicitly. Distinguish confirmed, tentative, unmatched, and withheld conclusions
internally. Only confirmed conclusions provide default-join identities. Keep the
existing stored statuses, evidence precedence, thresholds, assignment scope,
candidate retention, and linker version. Runtime recipes already identify changed
source bytes.

Share the deterministic reviewed-decision policy with publication. Keep durable
source continuity reads in the existing database reader. Keep placement updates,
confirmation watches, findings, history, revision changes, and work completion
inside the guarded commit. Preserve standalone and delegated stale-work behavior.

## Why

The former event function mixed identity policy with SQL and work bookkeeping.
Its nested writer still chose identities. Readers had to trace several parallel
dictionaries to explain one subject's result. Explicit conclusions make the
policy inspectable and testable without building a database fixture.

## Alternatives

- Extract more SQL helpers alone. This leaves the final identity choice inside
  persistence and does not provide an inspectable policy interface.
- Resolve each subject independently. This misses competing bib claims and
  contest/role assignment constraints.
- Introduce a configurable rule engine. The fixed precedence is clearer as
  ordinary ordered conditions; no changing rule language is required.

## Consequences

The implementation has more named data structures and smaller modules. Policy
tests exercise `resolve_event`; database tests retain coverage for evidence
loading, migration continuity, concurrency, and atomic writes. An internal
withheld conclusion still stores `unmatched`, preserving the table contract.
This refactor does not authorize additional identity joins or deployment.

## Links

- [Identity rules and implementation](../../docs/reference/identity-linking.md)
- [Validation outcome](../investigations/2026/link-resolution-refactor-2026-09-17.md)
- [Event resolver](../../src/swingset/link/resolution.py)
- [Public operation](../../src/swingset/link/service.py)
