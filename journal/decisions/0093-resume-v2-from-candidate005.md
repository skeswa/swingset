# D-0093: Resume v2 from candidate 005

Status: Accepted  
Recorded: 2026-09-17  
Accepted: 2026-09-17  
Acceptance source: Owner instruction in the continuation session  
Topic: V2 continuation and operational ownership  
Supersedes: D-0091's implementation pause  
Superseded by: —

## Decision

Resume the accepted history and recovery plan from the candidate-005 checkpoint.
Use bounded Luna implementation and independent review agents with distinct file
ownership. One coordinator owns integration, formatting, source freezes and all
production operations. Preserve retained evidence and concurrent work. Do not
commit or push without a further owner instruction.
Use the transient jj snapshot-size option when needed; the owner explicitly
excluded repository configuration changes in this continuation.

## Why

The owner explicitly resumed work after the pause. Existing D-0087 acquisition,
deployment and publication authority remains in force. Technical gates, explicit
year acceptance and H17 human adjudication remain separate. Rebuild and review
the schema-29 rehearsal packet against candidate 005 before execution; the
candidate-004 packet cannot establish acceptance of candidate 005.

The initial working copy was clean at parent `8d59db2b`. The earlier work is
already retained there; this continuation does not reconstruct old workspaces.

## Links

- [Previous pause](0091-pause-v2-after-current-validation.md)
- [Standing authority](0087-authorize-remaining-v2-acquisition-and-operations.md)
- [Continuation](../investigations/2026/v2-continuation-2026-09-17.md)
