# Preparing historical source parsers (V2)

> Historical record. Dates, status claims, and instructions below describe the
> original work. See [current status](../../../docs/status.md) before acting.
> [Project journal](../../README.md)

This is an offline parser review under the implementation plan's early
WP14–WP16 preparation exception. It does not open V5 or create watches.

## Completed: archived EEPro directory filenames

The retained FreedomSwing2019 directory capture has seven result links.
The previous extractor returned five: Apache abbreviated the visible
labels for `jackandjillprelimssemis.html` and `strictlyswingfinals.html`.
Their complete filenames are present in the link targets.

`eepro.autoindex` extract version 3 now obtains filenames and extensions
from the link URL path. Parser version remains 1. The actual 1,977-byte
body and provenance are in `tests/fixtures/sources/eepro-archive/`.
Regression tests verify all seven filenames, original URLs, modification
times, sizes, and in-memory child locators. A separate synthetic guard
checks truncated PDF labels, query strings, and non-file links.

The original WP11 fetch receipt remains unchanged. Its five-child
interpretation is superseded by
`workflow-output/v2-phase1/wp11-eepro2019/interpretation-review.json`,
which records all seven locators and hashes the original receipt. Rebuild
that review without network or database access:

```sh
.venv/bin/python -m journal.tools.collection.review_eepro_archive
```

The source version change requires the H6/H7 autoindex corpus to be
refreshed. The independent raw-body witness must account for full link
targets as well. This task did not fetch any of the seven score sheets.

## Acceptance still open

| Package                        | Retained evidence and preparation                                                                                                                      | Done criteria still unmet                                                                                                                                                                                                                                                          |
| ------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| WP14 Step Right Solutions      | Synthetic index/event/round tests pass. The retained research contains CDX rows and index/round 507/508 response headers, but no corresponding bodies. | Real fixtures must establish prelim promotion, mark `2` sub-values, and finals bib ownership. Canonical admission/projector integration remains pending. All 2009–2016 events with rounds must eventually parse or carry findings; synthetic tests do not establish that coverage. |
| WP15 Platform archive backfill | The retained EEPro 2019 index now yields all seven linked files. Existing platform parsers remain available for gated replay.                          | This index establishes no historical round-format coverage. EEPro, scoring.dance, DCN, and WDR sheets still need the gated, newest-first intake, admission, linking, and publication; coverage must report actual archive versus origin facts per year.                            |
| WP16 Origin gap fill           | No additional parser behavior was established in this review.                                                                                          | EEPro's pre-2018 operator answer, source-specific gap rules, reviewed event-site PDF locators and formats, proof that origin fallback never requests an archived page, and published gap disclosures remain necessary. No origin probing or acquisition was performed.             |

The per-year event acceptance gate and deployed H7/H10 gates remain in
force. No year was marked accepted by this work.
