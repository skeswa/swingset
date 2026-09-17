# D-0068: Decode DCN source data without running scripts

Status: Proposed  
Recorded: 2026-09-17  
Accepted: —  
Acceptance source: —  
Topic: Source interpretation  
Supersedes: —  
Superseded by: —

## Decision

Use a bounded data-only decoder for the observed Nuxt serialization instead of
the previously planned Node evaluator. Preserve the pure `evaluate_nuxt`
interface. Accept literal objects, arrays, strings, finite numbers, booleans,
null, parameter references and the observed `void 0` serialization behavior.
Reject executable or unknown grammar; never fall back to evaluation.

Implement only the real-controlled archive index and legacy event metadata
kinds. Preserve raw dates, affiliation/status flags and locators. Validate event
ownership and duplicate view agreement. Emit no watches and make no claim of
complete-year enumeration or publicly available results from metadata alone.

## Why

The approved index body contains a data-only function wrapper with 50 event rows;
all 50 match its rendered links. A general JavaScript runtime is unnecessary for
this verified grammar and would add isolation and subprocess requirements.
Explicit input, depth, node and expansion bounds make rejection predictable.
The older fixture is a different legacy metadata layout, not a modern results
payload. Treating both as results would invent unsupported facts.

## Consequences

New serialization forms require review. Modern results, score PDFs, other index
routes and year-list responses remain unimplemented or unverified. No source
kind, canonical projection, historical year or publication is accepted here.
Implementation proceeds under existing authority; the coordinator approved this
routine decoder choice in-session. This record does not invent owner acceptance.

## Links

- [Exact parser bounds](../../docs/reference/parsing.md#parsing-rules)
- [DCN source contract](../../docs/reference/sources/danceconvention.md)
- [Offline implementation receipt](../investigations/2026/dcn-offline-parsing-2026-09-17.md)
- [Index acquisition approval](0060-approve-exact-dcn-index-fixture.md)

## Record identity

Renumbered from D-0064 on 2026-09-17 to resolve a concurrent decision ID
collision. The decoding decision is unchanged; retained evidence keeps its
original references. D-0064 belongs to the participation-page decision.
