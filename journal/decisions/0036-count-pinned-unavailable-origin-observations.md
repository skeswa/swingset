# D-0036: Count pinned unavailable-origin observations

Recorded: 2026-09-16  
Decided by: agent  
Topic: Release coverage  
Supersedes: —  
Superseded by: —

## Decision

Populate `unavailable_pages` from bounded, pinned request observations. Any usable
retained origin or archive acquisition wins over an unavailable response. Otherwise,
require a complete snapshot search and a latest matching origin response classified
`Gone` or `ExpectedUnavailable`, with an HTTP error status and its retained body
verified. `Gone` requires 404 or 410. An archive error
alone does not establish origin unavailability. Compare aware response times;
conflicting latest origin outcomes leave the classification unknown.

The count describes observations at the release's verification time under its
source cutoff. Zero means no qualifying unavailable observation in a fully assessed
enumeration, not that every page was available. Incomplete searches, invalid times,
or unverifiable supporting bodies leave the total unknown. Keep unsupported-page
classification separate and unassessed.

## Why

Missing local artifacts, failed parsing, and absent queue rows do not prove that
the source reported a page unavailable. Retained response classifications and bodies
provide narrower evidence. Historical expected-unavailable classifications must not
be recomputed from a watch's mutable current success history.

## Consequences

Make classification opt-in on the shared request verifier so ordinary progress
checks do not spend extra artifact budget. Capture a new version of local release
coverage; retain compatibility with older witnesses, whose unavailable counts stay
unknown. Pin snapshot, watch, request, classification, transport, timestamp, HTTP
status, and body digest. Recheck exact positive unavailable support and its body at
validation, cache reuse, and publication. Later observations never rewrite the old
observation. No new source request, schema column, or production deployment is needed.

## Links

- [Event completion coverage](../../docs/reference/data-model.md#event-completion-coverage).
- [History and recovery plan](../../docs/plans/history-and-recovery.md).
