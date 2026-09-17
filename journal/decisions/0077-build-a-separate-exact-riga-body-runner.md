# D-0077: Build a separate exact Riga body runner

Status: Proposed  
Recorded: 2026-09-17  
Accepted: —  
Acceptance source: —  
Topic: Exact archived Riga results fixture runner  
Supersedes: —  
Superseded by: —

## Decision

Derive a new fixed-manifest packet from the retained, independently reviewed
schema-28 DCN metadata runner. Replace only its acquisition plan and associated
proof pins. Permit the exact Riga results capture authorized in D-0073 and
Archive robots: at most two requests, no metadata, redirects, retries, alternate
captures, PDFs, origin or children. Keep 2 MiB per response, 4 MiB total and the
15-minute execution window.

Package the verified CDX response in the sealed helper closure. Require its
exact row and hash when loading the manifest. Reject a response whose reported
Memento time differs from capture `20190719204919` or is absent. The requested
URL does not independently prove which capture the service returned. Retain any mismatched response as incomplete
quarantine evidence without accepting it as the requested capture.

## Why

The approved body needs separate authority from the prior metadata lookup. A
new packet preserves both operation histories and prevents metadata permissions
from carrying forward. The frozen runtime predates the new CDX evidence, so a
sealed local evidence copy provides a reviewable locator proof without changing
that runtime or fetching more metadata.

## Consequences

Preserve source 003, schema 28, exact public-baseline and hold checks, both schema
version fields, restore checks across writer-lock acquisition, H13 admission,
shared paid accounting and conservative legacy-spacing refusal. Preserve the
reviewed completion and monotonic gap, stricter active host rules, bounded body
reader and incomplete-response reservation. This operation cannot initialize
spacing authority or write observations, watches or source-kind activation.

The builder writes only a new packet. The preparer reads current state under
the writer lock and creates single-use authorization and execution gates. The
coordinator owns staging and execution after independent review. D-0073 supplies
owner acquisition authority; this implementation choice supplies none.

## Links

- [Owner approval](0073-approve-exact-riga-results-html-fixture.md)
- [Exact body proposal](../investigations/2026/dcn-results-body-proposal-2026-09-17.md)
- [Runner evidence](../investigations/2026/dcn-results-body-runner-2026-09-17.md)
- [Prior transport choice](0067-build-an-exact-schema28-dcn-metadata-lookup.md)
