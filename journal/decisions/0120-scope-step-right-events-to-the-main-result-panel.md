# D-0120: Scope Step Right events to the main result panel

Recorded: 2026-09-17  
Decided by: agent  
Topic: Step Right event parsing  
Supersedes: —  
Superseded by: —

## Decision

Read a Step Right event name and date from its dedicated event header. When a
results panel is present, pair each main-panel contest heading with its
following round list and exclude the responsive sidebar copy. Reject an empty
reviewed main panel instead of falling back to unrelated page links.

## Why

The independently reviewed Asian Open 2015 body contains six contests and 12
round links in the main panel, then repeats those links in the sidebar. The old
whole-page traversal emitted 24 links, assigned the sidebar copy to the wrong
contest, and used the breadcrumb as the event name. Scoping to the owned panel
preserves the printed structure without inventing coverage beyond this body.

This parser correction does not accept a source-kind contract, enable watches,
project canonical results or accept a historical year.

## Links

- [Step Right next controls](../investigations/2026/stepright-next-controls-2026-09-17.md)
- [Step Right body operation](../evidence/admission/stepright-body-2026-09-17/receipt.json)
- [Step Right source reference](../../docs/reference/sources/step-right-solutions.md)
