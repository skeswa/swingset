# D-0108: Build a separate Step Right body runner

Recorded: 2026-09-17  
Decided by: agent  
Topic: Step Right fixture acquisition  
Supersedes: —  
Superseded by: —

## Decision

Use a dedicated one-body Step Right adapter around the shared Archive gate,
accounting, response classification and storage primitives. Do not alter sealed
fixture helpers or copy the earlier multi-target runners.

The runner permits only the exact Asian Open 2015 replay body, plus required
robots traffic. It preserves an explicit ordinary-source disable and creates no
production watches, snapshots, observations or parser work.

## Why

The existing sealed helpers bind other fixture scopes. A narrow adapter reuses
their reviewed transport rules while keeping the Step Right URL, redirect,
Memento, request and quarantine limits explicit and independently reviewable.

## Links

- [Runner design](../investigations/2026/stepright-body-runner-design-2026-09-17.md)
- [Retained locator review](../investigations/2026/stepright-next-controls-2026-09-17.md)
- [Standing authority](0087-authorize-remaining-v2-acquisition-and-operations.md)
