# D-0069: Approve the exact DCN results metadata lookup

Status: Accepted  
Recorded: 2026-09-17  
Accepted: 2026-09-17, Sandile Keswa  
Acceptance source: Owner reply: “Approve this exact metadata lookup”  
Topic: Source fixture acquisition  
Supersedes: —  
Superseded by: —

## Decision

Approve the [exact Riga results-capture lookup](../investigations/2026/dcn-results-locator-proposal-2026-09-17.md).
Its machine proposal SHA-256 is
`c982f5124b396783eff8b4565600358127667e85f51a96a94255190e4a26734c`.
The ceiling is two Archive metadata queries for that one printed results URL,
three HTTP requests including robots, 2 MiB per response, 4 MiB total and
15 minutes. There are no page bodies, PDFs, redirects, retries, alternate
queries, origin requests, children or production acquisition.

Independent runner review and current source, schema, budget, hold and host
checks remain execution gates. This accepts no historical year, page kind,
watch, interpretation or publication. Any returned body locator needs a
separate exact acquisition proposal.

## Why

The retained controls do not identify an archived results page or score PDF.
D-0053's two queries were consumed and D-0060 permitted no metadata queries.
The owner supplied this separate bounded decision to locate review material.
