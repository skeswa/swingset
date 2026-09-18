# D-0022: Preserve supported event bases

Recorded: 2026-09-16  
Decided by: agent  
Topic: Event preservation  
Supersedes: —  
Superseded by: —

## Decision

When a scoring listing generates an existing inventory-backed event ID, retain
the complete stored event, its registry reporting month, and its ownership.
Use the existing inventory predicate: retained history sources or ownership by
the history inventory. Provisional source-only events can still refresh.

During release reconstruction, a rejected legacy event overlay must not remove
an independently admissible structural event with the same key. Resolve this
after all selected rows are considered, so ordering cannot change the outcome.
Retain rejection counts. Explicit revocation and unsupported result rows still
withhold their event; a partial history patch cannot create a structural base.

## Why

The scratch audit found that mapping overwrote 208 registry occurrence identities.
Reconstruction then let rejected event alternatives veto supported event bases,
removing named judges whose source evidence remained. Missing WSDC numbers are
not grounds to remove a judge's name. These defects predate the memory fix.

## Consequences

Test both structural-row orders, provisional updates, registry associations,
null-ID judges, and continued revocation and unsupported-result vetoes. A new
frozen runtime must undergo normal acceptance and full scratch rederivation;
the package-wide runtime recipe invalidates the previous project/link receipts.
Do not transplant or relabel old generations to avoid that replay. Production
deployment and publication remain held.

## Links

- [Audit diagnosis](../evidence/releases/h16-validation-2026-09-16/audit-failure-diagnosis.md).
- [Build investigation](../investigations/2026/h16-proof-reuse-2026-09-16.md).
