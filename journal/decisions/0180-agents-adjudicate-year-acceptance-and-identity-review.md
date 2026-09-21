# D-0180: Agents adjudicate year acceptance and identity review

Recorded: 2026-09-21  
Decided by: owner, 2026-09-21; source: "I want to adjust plan to use agents to decide on data integrity/quality instead of humans. I think Opus subagents are more than capable enough of judging year acceptance based on evidence and human identity based on context clues and basic research tools. Let's get V5 and V6 definitely done"  
Topic: Review authority  
Supersedes: [D-0055](0055-defer-human-adjudication-to-a-review-website.md); the "owner's call" for year acceptance in the backfill reference; the human-only wording of the H17 identity evaluation  
Superseded by: —

## Decision

Two judgments the plan reserved for humans are now made by review agents,
recorded the same way a human review would be:

- **Year acceptance.** An agent reviews a year's open findings against the
  retained evidence, resolves them through the override files or records why
  a finding stays open, and accepts the year with `accepted_by` naming the
  agent and the session. The acceptance precondition is unchanged: no open
  history findings for the year and no pending inventory projection.
- **Identity review (H17).** An agent adjudicates each sampled subject from
  the review packet's frozen evidence plus the local dataset, writes the
  adjudication rows the evaluation tool defines, and the precision report
  those rows produce is the reviewed evaluation. `reviewer` and `method`
  name the agent and its inputs.

Review agents use retained evidence, the local database and the published
dataset, and may use web search to verify or deduplicate an event or a
person when the retained names are not enough (owner, 2026-09-21). They do
not fetch from the project's source sites outside the pipeline's rules. A
subject that still cannot be decided is `insufficient_evidence`, and a
listing that cannot be placed is left as an open finding with a note.

## Why

Human review was the one step in V5 and V6 with no one assigned, and it held
every historical year and the identity precision estimate for eight days.
The evidence for both judgments is already frozen and readable, and the
owner judges review agents capable of weighing it. Recording who decided
keeps every judgment reversible.

## Links

- [Backfill reference, events first](../../docs/reference/backfill.md#events-first)
- [Identity linking, H17](../../docs/reference/identity-linking.md#offline-reviewed-evaluation-h17)
- [History and recovery plan](../../docs/plans/history-and-recovery.md)
