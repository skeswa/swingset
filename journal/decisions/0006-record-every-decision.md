# D-0006: Record every decision in the decision log

Status: Accepted  
Recorded: 2026-09-15  
Accepted: 2026-09-15, Sandile Keswa  
Acceptance source: Owner instruction: “Make sure that ALL decisions get recorded in the decisions log”  
Topic: Documentation  
Supersedes: D-0001's restriction to lasting decisions  
Superseded by: —

## Decision

Record every decision and its reason in `journal/decisions/` in the same change,
including routine implementation choices. Give each record a stable ID and
index entry. Keep small records brief while retaining status and acceptance
fields. Update affected docs proactively in the same change.

## Why

The owner requires a complete decision log. The earlier rule excluded small
choices and left their reasons scattered across conversations and change
descriptions.

## Alternatives

Recording only lasting choices keeps the log smaller but omits decisions the
owner wants preserved. Brief records keep the broader log manageable.

## Consequences

The log will grow faster. Each choice remains findable, with its reason and
actual acceptance source. Recording does not add an approval step to already
authorized work or turn an agent recommendation into owner acceptance.

## Links

- [Earlier decision](0001-documentation-and-decisions.md)
- [Decision process](README.md)
- [Agent instructions](../../AGENTS.md#keep-docs-and-decisions-current)
- [Writing guide](../../docs/writing.md#record-decisions-and-investigations)
