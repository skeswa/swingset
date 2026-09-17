# D-0097: Keep manual registry artifacts on their replay path

Status: Proposed  
Recorded: 2026-09-17  
Accepted: —  
Acceptance source: —  
Topic: Input invalidation routing  
Supersedes: —  
Superseded by: —

## Decision

Exclude only the exact manual registry cross-check artifact from ordinary
runtime-recipe parse invalidation. Its watch has source `crosscheck`, kind
`registry_dump`, method `MANUAL` and parser sentinel `registry_crosscheck`.
Retain its body and dedicated `replay_crosscheck()` path. Normal snapshots must
still invalidate, and other unknown parsers must remain visible failures.

Do not register a fake page kind or silently discard existing blocked attempts.
Any already queued manual artifact needs a separately reviewed reconciliation;
its historical failure remains evidence.

## Why

The second actual candidate-005 scratch replay selected a manually archived
registry dump as ordinary parse work. Runtime input invalidation had enqueued
all snapshots, while page-kind lookup correctly refused the manual sentinel.
The ownership boundary belongs in invalidation, which already knows which
artifact should be replayed. This avoids weakening parser admission to hide the
observed exception.

The change belongs to a successor source revision. Candidate 005 and its
successful validation/rehearsal receipts remain unchanged. This engineering
choice does not grant operating acceptance or any new source/year authority.

## Links

- [Diagnosis](../investigations/2026/manual-registry-replay-routing-2026-09-17.md)
- [Observed blockers](../evidence/runtime/schema29-successor-rehearsals-2026-09-17/inputs-review-001/drain-002-blockers.json)
