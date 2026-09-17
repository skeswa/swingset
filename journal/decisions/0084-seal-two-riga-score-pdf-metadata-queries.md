# D-0084: Seal two exact Riga score-PDF metadata queries

Status: Proposed  
Recorded: 2026-09-17  
Accepted: —  
Acceptance source: —  
Topic: Fixture runner implementation  
Supersedes: —  
Superseded by: —

## Decision

Build a new, separately sealed runner for the two exact score-PDF metadata
queries in the approved proposal. Bind the original HTML, acquisition audit,
locator context, proposal and standing D-0087 authority in its helper closure.
Reuse the reviewed schema-28 transport, H13 admission and accounting wrapper.
Change only its exact manifest, authority binding and two-query iteration.

Save a separate receipt entry for each URL before its probe, after the probe,
and after optional page zero. If the second lookup stops, preserve the first
lookup's evidence and the second lookup's unfinished state. Missing pages
remain unexamined. The runner never requests PDFs, extra CDX pages, redirects,
retries, alternate locators or origins.

## Why

The prior runner was sealed to one different metadata URL. Reusing its packet
would expand a completed operation's scope. A new closure keeps the standing
authority separate from concrete acquisition bounds and permits independent
review before coordinator execution. Recording each query separately avoids
mistaking one completed URL for completion of both.

The 8 MiB aggregate ceiling can stop before all five permitted HTTP requests.
A grant already paid before a zero-capacity stop stays paid. This preserves the
existing conservative accounting rule; the runner neither refunds uncertainty
nor increases the shared Archive budget.

## Links

- [Standing authority](0087-authorize-remaining-v2-acquisition-and-operations.md).
- [Runner evidence and limits](../investigations/2026/dcn-score-pdf-lookup-runner-2026-09-17.md).
- [Exact metadata proposal](../investigations/2026/dcn-score-pdf-lookup-proposal-2026-09-17.md).
