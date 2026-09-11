# WSDC rules audit evidence

Evidence for [the September 11 audit](../../../wsdc-rules-audit-2026-09-11.md).

`cache-manifest.json` identifies 54 pre-existing source files by relative
cache name, byte count, and SHA-256. It also maps citation keys to original
and replay URLs, records the target document hashes before and after the
audit, and records the unsuccessful fresh HTTP checks. Duplicate files and
an excluded draft are inventoried; the count is not a count of distinct
operative rules.

The CDX JSON files were copied from the earlier source-discovery cache.
They are archive indexes, not fresh response bodies. They establish replay
identifiers, not byte identity between an indexed capture and a local PDF.
No prior HTTP response headers were available for the source cache. Source
bodies remain outside the repository, consistent with the verification
folder's policy.

To reproduce a claim check, follow the exact source/replay link in the
history, inspect the cited printed page or section, and compare the downloaded
file's SHA-256 with the corresponding cache entry when identifiable. A hash
mismatch requires inspecting whether the source was republished or the replay
changed. Future retrievals must use the repository's fetching rules.

`validation.json` records offline checks of citation-key coverage, archive
link precision, local link targets, retained source hashes, and table shape.
It does not report successful fresh HTTP availability or prove substantive
historical completeness.
