# D-0060: Approve the exact DCN index fixture

Recorded: 2026-09-17  
Decided by: owner, 2026-09-17, Sandile Keswa; source: Owner reply: “Approve this exact index fixture”  
Topic: Source fixture acquisition  
Supersedes: —  
Superseded by: —

## Decision

Approve the single archived DCN index body at capture `20251112105828` in the
[exact proposal](../investigations/2026/dcn-index-fixture-proposal-2026-09-17.md).
The ceiling is five HTTP requests, 16 MiB total and 15 minutes, including any
robots request and at most three Archive redirects. There are no PDF, child,
CDX, origin, alternate or retry requests and no production acquisition.

The spacing fix and independently reviewed runner remain execution gates.
Use fresh quarantine, the unchanged shared Archive budget and the actual
runtime and publication pins. This accepts no year or source page kind.

## Why

The approved metadata lookup supplied an exact index capture. A complete real
body is needed to review DCN's index format and locators. D-0053 excluded these
bodies, so the owner supplied this separate bounded decision. The request-timing
finding must be fixed before this new operation.

## Links

- [Independent capture audit](../investigations/2026/fixture-controls-review-2026-09-17.md)
- [Earlier fixture exception](0053-approve-exact-new-source-fixture-exception.md)
