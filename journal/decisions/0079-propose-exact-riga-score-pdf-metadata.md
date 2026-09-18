# D-0079: Propose exact Riga score-PDF metadata lookup

Recorded: 2026-09-17  
Decided by: agent  
Topic: Source fixture acquisition  
Supersedes: —  
Superseded by: —

## Decision

Propose a separate Archive metadata lookup for the two score-PDF URLs printed
in the audited Riga Newcomer results HTML: finals `3451330.pdf` and prelims
`3451331.pdf`. Permit at most one page-count probe and page zero per exact URL,
plus one robots check: five HTTP requests, 2 MiB per response, 8 MiB total and
15 minutes. Permit no PDF or archived-body request, redirect, retry, alternate
URL, origin request or child request. Owner approval is still missing.

Retain actual CDX MIME types without assuming that a `.pdf` URL has a PDF
response. Preserve the existing host floor, controls and shared budget. A
new independently reviewed fixed-manifest runner must bind the then-current
runtime and held state before execution.

## Why

The source now provides concrete score-file locators. Neither their Archive
captures nor their bodies are known. Earlier approvals covered different
metadata or the HTML body only. Separating metadata from body acquisition
keeps the next decision exact and reviewable, without guessing round IDs or
expanding a fixture exception into production collection.

## Links

- [Proposal, evidence and exact limits](../investigations/2026/dcn-score-pdf-lookup-proposal-2026-09-17.md).
- [Fetching contract](../../docs/reference/fetching.md).
