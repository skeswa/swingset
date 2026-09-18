# D-0073: Approve the exact Riga results HTML fixture

Recorded: 2026-09-17  
Decided by: owner, 2026-09-17, Sandile Keswa; source: Owner reply: “Approve this exact results HTML fixture”  
Topic: Source fixture acquisition  
Supersedes: —  
Superseded by: —

## Decision

Approve the [exact Riga results HTML fixture](../investigations/2026/dcn-results-body-proposal-2026-09-17.md)
at Archive capture `20190719204919`. The machine proposal SHA-256 is
`17dea52f53b41b04093f4582a94da0f58afbe808408304f9681e2f3750575483`.
The ceiling is one exact archived HTML body, two HTTP requests including robots,
2 MiB per response, 4 MiB total and 15 minutes. No PDFs, metadata queries,
redirects, retries, alternate captures, origin or child requests are authorized.

Independent runner review and current source, schema, hold, robots, spacing and
shared-budget checks remain execution gates. This accepts no historical year,
new source kind, interpretation, watch or publication.

## Why

The separately approved metadata lookup returned this exact capture of the
source-printed Riga results URL. A complete real body is needed to inspect its
results controls and any printed score-file locators. D-0069 explicitly excluded
body acquisition; the owner supplied this separate bounded decision.
