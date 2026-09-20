# D-0172: Delegate all project decisions to agents until revoked

Recorded: 2026-09-20  
Decided by: owner, 2026-09-20; source: session instruction "Until further notice, you do not need owner approval for any decisions"  
Topic: Documentation  
Supersedes: [D-0169](0169-agents-decide-and-record-and-ask-only-for-high-impact-choices.md) for its owner-approval threshold  
Superseded by: —

## Decision

Agents may make and record every project decision without first asking the
owner. This delegation lasts until the owner revokes it. Choices should favor
dataset accuracy, dataset coverage, repository maintainability and clarity,
and lower operating resource use.

Every choice still needs a decision record with its authority, reasons,
consequences, and supporting evidence. Decision authority does not by itself
authorize deployment, publication, purchases, third-party communication, or a
destructive production operation; those actions need authorization in the
task or another standing instruction.

## Why

Waiting for owner review slows work whose direction is already clear. The
owner has supplied the goals that agents should use when choices conflict.
Keeping the decision log preserves reviewability without making review a
prerequisite.

## Consequences

The high-impact categories remain useful labels for review and documentation,
but they are no longer approval gates. An agent may reverse an earlier
owner-decided choice when the evidence and project goals support it, and must
record the replacement in a new linked decision.

## Links

- [Decision index](README.md)
- [Repository instructions](../../AGENTS.md)
