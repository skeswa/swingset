# D-0003: Keep source evidence separate from derived records

Recorded: 2026-09-15; imported from existing architecture  
Decided by: owner, —; original acceptance date and approver not established by this review  
Topic: Architecture  
Supersedes: —  
Superseded by: —

## Decision

Archive source responses and parse them into source-specific observations.
Combine those observations into shared event records in a separate step.
Resolve person identities after that. Preserve enough evidence to explain
and rebuild published results.

## Why

Source layouts, event mappings, and identity decisions can change independently.
Keeping the evidence lets Swingset repeat local work without asking websites
for unchanged pages or losing the basis for an earlier result.

## Alternatives

Writing final dataset rows directly from each fetched page would shorten the
pipeline. It would also couple source parsing to event matching and make
conflicting pages and later corrections harder to handle. This alternative is
reconstructed from the architecture; the original deliberation was not retained.

## Consequences

The pipeline has more explicit stages and retained state. It must track which
inputs support each result. In return, changes can be explained and affected
records rebuilt from saved evidence.

## Links

- [Architecture contract](../../docs/reference/architecture.md)
- [Parsing contract](../../docs/reference/parsing.md)
- [Local state](../../docs/reference/state.md)
