# D-0045: Account for evidence-backed unsupported pages

Recorded: 2026-09-17  
Decided by: agent  
Topic: Event accounting and release coverage  
Supersedes: —  
Superseded by: —

## Decision

Use verified critical-unknown source-contract reports as one explicit unsupported
page disposition. Pin the exact generation, contract and complete body/extract
manifest. An older usable interpretation takes precedence. Reuse the bounded
request verifier for local inventory, sampled accounting and release coverage.
Retain unknown results when evidence cannot be fully assessed. Require one manifest
member until aggregate reports can attribute unknown fields to individual requests.

## Why

A generic parse failure or an absent parser does not establish an unsupported
layout. Typed critical field accounting supplies inspectable evidence without
admitting that interpretation. The unavailable-origin verifier already provides
a separate gap branch; neither branch creates successful progress. Raw acquisition
can independently succeed even when interpretation is unsupported.

## Consequences

Schema 25 adds generation-domain invalidation for replaceable gap observations.
Existing observations become stale under the new observer policy. Version-three
local release witnesses retain positive unsupported proofs; older witnesses keep
unsupported counts unknown. Every release boundary rechecks their artifacts,
including when SQLite-only closure proof is cached.

Projection-only unsupported scoring remains a contest finding alongside valid
source interpretation. Mixed pages are not relabeled wholesale. Additional typed
dispositions and full unsupported-page acceptance remain unfinished. This record
does not approve a new source kind, change an admission gate, or record owner
acceptance. Implementation proceeds under the existing request to continue v2.

## Links

- [Coverage contract](../../docs/reference/data-model.md#event-completion-coverage)
- [Implementation and validation](../investigations/2026/unsupported-event-pages-2026-09-17.md)
- [Accepted extension](../../docs/plans/recovery/README.md)
