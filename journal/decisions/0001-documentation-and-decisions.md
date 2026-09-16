# D-0001: Separate current guidance, research history, and formal decisions

Status: Accepted  
Recorded: 2026-09-15  
Accepted: 2026-09-15, Sandile Keswa  
Acceptance source: Owner instruction in this documentation-reorganization session: “Ok! I like it - Please re-organize the codebase” after reviewing the structure and decision process.  
Topic: Documentation  
Supersedes: —  
Superseded by: [D-0005](0005-research-under-journal.md), for research placement; [D-0006](0006-record-every-decision.md), for decision scope

> The evidence-location choice below is replaced by [D-0005](0005-research-under-journal.md).
> [D-0006](0006-record-every-decision.md) expands recording to every decision.
> The reading hierarchy and remaining decision process stay in force.

## Decision

Use `docs/` for current explanations, task guides, exact rules, and active
plans. Use `journal/` for research narratives, dated outcomes, and formal
project decisions. Keep reproducible scripts and captured evidence in
`research/`. Retire `design/` as a separate documentation category.

Teach the project from the top down. Each parent page should stand on its own
and link to deeper detail. Write for a new reader at a high-school reading level.

Give lasting decisions stable IDs and explicit acceptance. Preserve historical
choices without inventing missing approvers or dates. The
[decision process](README.md) defines the statuses and required record fields.

## Why

The documentation review found current rules mixed with future plans and old
release receipts. Repeated identity rules disagreed. Readers needed knowledge
of work-package IDs before they could follow the project.

## Alternatives

- **Keep the folders and add a larger index.** This helps discovery but leaves
  current guidance mixed with history and competing copies of rules.
- **Store decisions only inside investigations.** This preserves context but
  makes acceptance and replacement hard to find.

## Consequences

Each exact rule needs one owner. Behavior changes must update the relevant
reference. Decision reasons remain in the journal; implementation and deployment
status remain separate. Moving pages requires updating links and documentation
output paths. Captured evidence retains its original bytes and historical paths.

## Links

- [Documentation guide](../../docs/README.md)
- [Writing and ownership rules](../../docs/writing.md)
- [Reorganization record](../investigations/2026/2026-09-15-documentation-reorganization/README.md)
