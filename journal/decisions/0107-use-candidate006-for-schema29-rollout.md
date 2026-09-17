# D-0107: Use candidate 006 for the schema-29 rollout

Status: Proposed  
Recorded: 2026-09-17  
Accepted: —  
Acceptance source: —  
Topic: Runtime rollout candidate  
Supersedes: —  
Superseded by: —

## Decision

Prepare the next schema-29 production gate for candidate 006. Do not deploy
candidate 005. Candidate 006 keeps candidate 005's migration and scheduler
runtime, adds the reviewed DCN PDF parser, and fixes the manual registry replay
routing defect exposed by candidate 005's scratch drain.

Build, service binding, rehearsal packets, migration helpers and live gates must
bind candidate 006's exact source. Candidate-005 receipts remain valid evidence
for those exact bytes but do not certify candidate 006.

## Why

Candidate 005 is tested and built, but its actual replay routed a manual
`registry_crosscheck` artifact into the ordinary parser. Candidate 006 passed a
full 2,648-test validation with that narrow routing fix. Deploying candidate 005
would knowingly retain the defect and require another runtime rollout.

This selection is an authorized implementation step under D-0087 and D-0093;
it is not owner acceptance of the parser or routing decisions, a source-kind
activation, a historical-year acceptance, a deployment or a publication.

## Links

- [Candidate history](../../docs/reference/candidate-history.md)
- [Manual routing diagnosis](../investigations/2026/manual-registry-replay-routing-2026-09-17.md)
- [Candidate 006 validation](../evidence/runtime/event-extension-2026-09-17/validation-006/validation.json)
- [Standing authority](0087-authorize-remaining-v2-acquisition-and-operations.md)
