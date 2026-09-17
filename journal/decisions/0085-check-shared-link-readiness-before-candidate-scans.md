# D-0085: Check shared link readiness before candidate scans

Status: Proposed  
Recorded: 2026-09-17  
Accepted: —  
Acceptance source: —  
Topic: Measured ordinary offline selection cost  
Supersedes: —  
Superseded by: —

## Decision

When the ordinary selector owns its read snapshot, check the database-defined
shared dancer prerequisite before entering a link group's candidate scan. A
false result skips that link group only. A true result follows the unchanged
candidate-currentness, event-readiness, retry, exclusion and control checks.
Keep group ordering and complete known-scope fallback unchanged.

Do not apply this early check in a caller-owned transaction. Consistency groups
require such a transaction and retain their existing individual prerequisite
behavior. Older schemas without derivation support keep their existing path.
No worker or derivation-group semantics change.

## Why

[Comparison 003](../evidence/runtime/offline-selector-profile-2026-09-17/comparison-003/report.json)
still stopped at its 30-second limit. The shared dancer result removed repeated
full proof loading, but link candidate currentness still spent 16.27 cumulative
seconds expanding dependencies before readiness could reject the same group.
The profile recorded over 62 million dependency-generator iterations. A
concurrent backup affected this observation's operating conditions; it is not a
production throughput estimate.

Every ordinary link requires the same dancer cohort, so a false answer is a
necessary prerequisite failure for the entire group in that snapshot. Checking
it before candidate currentness avoids repeated work without making the queue
authoritative, removing fallback scopes, or declaring a link current.

## Consequences

The existing snapshot-scoped proof supplies exact registered, queued and
physical dancer membership. No result survives snapshot closure. A changed
cohort can reopen selection in the next snapshot. The test reference retains
the previous full scan and must select the same healthy work.

Fresh worker admission remains required after selection. Local tests and a
source-bound scratch profile do not establish deployed service objectives,
production throughput, stage acceptance or publication.

## Links

- [Earlier shared proof choice](0081-check-shared-link-prerequisites-before-event-expansion.md)
- [Profile and validation evidence](../investigations/2026/offline-selector-profile-2026-09-17.md)
