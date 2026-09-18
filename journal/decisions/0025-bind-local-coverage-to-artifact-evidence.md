# D-0025: Bind local release coverage to artifact evidence

Recorded: 2026-09-16  
Decided by: agent  
Topic: Release coverage  
Supersedes: —  
Superseded by: —

## Decision

Extend the pinned source-event witness with bounded request evidence captured
inside the release's read snapshot. Share one verification budget across the
capture. Report an acquired or interpreted count only when every enumerated
member is known for that stage; otherwise report null and the uncertainty.
Keep selected interpretation and represented-result counts separate. Do not
infer unavailable or unsupported outcomes from missing artifacts.

Move the existing bounded file/row reader and request verifier into shared
admission modules. The scheduling probe and release verifier reuse those
primitives; neither duplicates artifact checks or imports the other's policy.
A session shares budgets and verified content only within one bounded call.

## Evidence and validation

Pin exact request, snapshot, generation, decision, policy, and artifact receipts.
Record verification time from the injected clock separately from source cutoff.
Pin the shared capture and validation limits once in the witness; include their
policy in semantic identity. A negative observation
describes the check that was performed; it cannot promise continued absence.
New arrivals or restored artifacts belong to a successor's observations.

Require an explicit connection-bound artifact provider when validating the new
witness. Reverify every positive artifact before the database proof-cache check
at every validation boundary, including completion and publication. Missing,
changed, or unverifiable positive support rejects the proof. File metadata is
not a substitute for content verification. Include relevant evidence outside
the selected output graph in the database read set without selecting new output.

Exclude check timestamps from semantic reuse identity; include material verdicts
and supporting evidence. Old witness versions retain their existing behavior.

## Consequences

Large histories may remain unknown when the bounded scan cannot decide. Tests
must mutate files while leaving SQLite unchanged, including later completion
checks and reuse/publication paths. This extension remains outside the frozen
schema-14 preservation replay and cannot change that release's scope.

## Links

- [Cutoff verification](0021-verify-page-evidence-at-a-release-cutoff.md).
- [Pinned enumeration evidence](0020-pin-event-enumerations-in-release-evidence.md).
- [Coverage contract](../../docs/reference/data-model.md#event-completion-coverage).
