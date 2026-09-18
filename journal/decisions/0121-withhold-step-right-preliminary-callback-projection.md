# D-0121: Withhold Step Right preliminary callback projection

Recorded: 2026-09-17  
Decided by: agent  
Topic: Step Right canonical projection  
Supersedes: —  
Superseded by: —

## Decision

Retain Step Right preliminary callback marks in source observations but do not
project them into canonical callback marks or outcomes. Keep promotion unknown.
Project only fields whose ownership and meaning the retained controls establish.

## Why

The source legend proves `1 = yes`, `2 = alt`, and `3 = no`, where `alt` is
unranked. The public canonical enum has only ranked `alt1` through `alt3`.
Mapping the printed value to `alt1` would invent rank, while adding a new public
enum value is a separate model decision. Withholding the canonical mark keeps
the source evidence without overstating it.

This proposal grants no source-kind policy, watch, historical-year acceptance
or publication.

## Links

- [Step Right source reference](../../docs/reference/sources/step-right-solutions.md)
- [Step Right next controls](../investigations/2026/stepright-next-controls-2026-09-17.md)
