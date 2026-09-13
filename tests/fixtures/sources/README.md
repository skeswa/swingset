# Source fixtures

Files named `synthetic_*` are minimal, hand-written contract fixtures. They are
not archived responses and do not verify undocumented source behavior. Real
fixtures require a recorded body from the pipeline archive; EEPro live capture
is deliberately deferred until the operator prerequisite is complete.

`recorded_wsdc_cdx.json` is the unchanged CDX response captured on 2026-09-11, copied from `research/verification/2026-09-11/wsdc-rules/cdx_worldsdc_rules.json`. It exercises the transport index format; historical event-list HTML/PDF bodies were deleted after research and are not available as recorded fixtures.
