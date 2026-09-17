# DCN capture audit and offline parsing, 2026-09-17

The separately approved DCN index body was acquired and retained; it is not a
production observation or publication. The
[independent audit](../../evidence/admission/dcn-index-review-2026-09-17/audit.json)
verifies both response hashes and lengths, canonical authorization/manifest,
exact allowlist and capture, and all request/byte/duration bounds. Two requests
received 2,804,642 bytes, including the robots 404. The selected index body is
2,804,496 bytes, SHA-256
`3e4e9cae6d39e099b79c7b5e7bae1e25c02fb0315e82e1311c7be4b39e3796bb`.

Measured completion-to-next-dispatch spacing was 10.0207652610261 elapsed
seconds and 10.020759 UTC seconds. The initial monotonic floor was
10.013764675997663 seconds. These verify the new completion/monotonic boundary;
they do not alter the earlier five-body run's two issue-time shortfalls.

## Real controls and local implementation

The index's 65,868-character Nuxt payload contains two matching render-data
views. Its 50 event rows exactly match all 50 rendered event links. There are
27 `WSDC` affiliations and 36 true source `results` flags. Available-year labels
run from 2025 through 2013. A separate
[locator report](../../evidence/admission/dcn-index-review-2026-09-17/locator-report.json)
retains the printed event locators and flags without creating requests or watches.
This is a listed population, not complete DCN history or an accepted year.

The new decoder reads the verified literal/function-wrapper serialization
without executing JavaScript, using the exact bounds in
[the parsing contract](../../../docs/reference/parsing.md#parsing-rules).
Calls, property access, statements, arithmetic, unknown references, duplicate
keys, malformed arguments and excessive expansion reject. `void 0` occurs in
an unrelated timezone field and follows JSON serialization semantics. The
index extractor validates its exact route, duplicate view consistency,
fields/types and event-ID ownership. Optional image fields remain nullable;
raw names, dates, locations, affiliations and flags are preserved.

The earlier complete legacy Tapestry body supplies Riga Summer Swing metadata
for August 9–13, 2018, with dates kept as printed. Desktop/mobile headers must
agree. A source results-tab link becomes an unassessed locator after removing
the archived routing session suffix. No identity, result availability, judge,
round, bib or score is inferred. Neither retained body contains a concrete
PDF locator. Further results/PDF acquisition still needs exact separately
approved proposals; no guessed identifier was probed.

Two raw fixture copies are byte-exact and provenance-pinned. Empty/malformed
fixtures and explicitly synthetic changed-input derivatives are separate from
those originals. No retained evidence was edited in place.

## Verification and limits

Fresh focused validation passed 94 tests: 59 DCN decoder/real-control tests and
35 existing Step Right tests. Ruff passed for the new source/tests; mypy passed
for all four DCN source-package files. Tests cover full inventory comparison,
round-trip records, Unicode/raw values, false flags and missing optional fields,
changed consumed fields, irrelevant scripts, wrong source/kind/URL ownership,
unknown grammar, ambiguous payloads, malformed rows, empty output, inconsistent
views, and byte/depth/node/alias-expansion bounds. All tests are offline.

Independent review found two defects and prompted fresh regressions. Invalid
URL syntax now becomes a controlled extraction/parse failure, including
whitespace/control characters. Payload record names now register the exact
persisted kind at import, rather than relying on the encoder learning an alias.
A fresh interpreter decodes both real payloads before any encode operation.

The independent follow-up retained a
[source-bound review receipt](../../evidence/admission/dcn-index-review-2026-09-17/independent-review-001/checks.json):
59 DCN tests and Ruff passed, and source/fixture hashes were unchanged before
and after the checks. Both review findings were resolved. The next missing
results/PDF controls have a separately scoped
[metadata-lookup proposal](dcn-results-locator-proposal-2026-09-17.md); no request
is implied by preparing that proposal.

This local increment was excluded from the coordinator's extension rollout
freeze. It is not deployed, published, registered for ordinary acquisition or
admitted by a source-kind policy. [D-0068](../../decisions/0068-decode-dcn-source-data-without-running-scripts.md)
records the decoder choice. Independent code review and later integrated
validation require their own receipts. Modern event-results payloads, legacy
results HTML, score PDFs and other listing/year routes remain real gaps.
