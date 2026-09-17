# Counting retained unavailable responses

The local H16 extension now counts explicit unavailable-origin observations in
release coverage. This work is outside the frozen production H16 source. It
has not been deployed or published.

## Evidence and meaning

The request verifier optionally classifies unavailable responses using its
existing candidate domain and cumulative budget. A usable retained origin or
archive body takes precedence. Otherwise it requires a complete bounded search,
a latest origin `Gone` or `ExpectedUnavailable` response with an HTTP error
status, and a digest-verified retained body. `Gone` requires HTTP 404 or 410.
Conflicting latest outcomes, invalid timestamps, incomplete searches, and
missing or corrupt supporting bodies remain unknown.

The count measures observations under the pinned source cutoff at artifact
verification time. Zero means no qualifying unavailable observation among the
assessed members. Missing local evidence does not establish source availability.
Archive errors alone do not establish origin unavailability. Historical
expected-response classifications are retained rather than recomputed from the
watch's current success history.

`release-local-pages-v2` pins the exact negative response and HTTP status. Its
metadata joins the release read set; its body is checked before a cached SQL
proof can be reused and at publication. An older usable body's later restoration
changes the next observation without rewriting the old one. Version-one local
witnesses remain valid with their unavailable counts unknown. Unsupported-page
classification remains separate and unassessed.

## Validation

The independent reviewer ran 96 focused tests across unavailable coverage,
existing local release coverage, and the shared page verifier. All passed in
12.87 seconds. The cases include origin versus archive responses, older usable
evidence, response ordering and ties, incomplete budgets, corrupt artifacts,
legacy witnesses, and actual cached-proof/publication rejection after the
supporting unavailable body disappears without a database change.

Combined schema-23 validation passed all 2,014 tests in 420.71 seconds, Ruff,
and mypy over 205 source files. Source and test hashes stayed unchanged. See
[validation 011](../../evidence/runtime/event-completion-2026-09-16/event-recovery-validation-011.json).
The retained first run, validation 010, had 2,013 passing tests and one outdated
assertion expecting all unavailable counts to remain null. The updated assertion
requires the new fully assessed zero count and preserves unsupported counts as
unknown. No runtime change was required for that correction. These receipts
establish local acceptance, not production or publication.

## Links

- [Coverage contract](../../../docs/reference/data-model.md#event-completion-coverage).
- [D-0036](../../decisions/0036-count-pinned-unavailable-origin-observations.md).
- [Current status](../../../docs/status.md).
