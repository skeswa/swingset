# Unsupported source pages: local implementation, 2026-09-17

The local runtime now accounts for one explicit unsupported-page disposition:
a verified critical-unknown source-contract report. It is implemented and tested
offline. It has not been deployed or published. This increment does not complete
the broader unsupported-page or event-completion acceptance contract.

## What changed

The shared bounded request verifier can pin a generation whose report names a
failed `critical_unknown` guard and a critical unknown field with a path and
reason. The current page-kind review, full manifest, body and extract bytes,
generation hashes, source/request identity and cutoff must verify. Revocations
reject old support. A retained usable interpretation takes precedence.

Fresh inventory and sampled accounting expose unsupported separately from
unavailable, acquired and interpreted. The union can account for a page obligation;
it creates no successful operation, progress receipt, retirement or publication.
Ordinary body verification can independently establish acquisition for that page.
An exhausted domain without usable acquisition cannot supply this unsupported
proof. Its zero count means no qualifying observation, not universal parser support.
Incomplete searches, corrupt generation evidence and unavailable interpretation
artifacts leave classification unknown where a proof might exist.

Schema 25 invalidates sampled gaps when generations are inserted or changed,
including unadmitted generations, or when a source unit changes watches. Observer
policy v5 invalidates prior samples; no unsupported history is inferred from older
records. Version-three release-local witnesses pin exact unsupported support.
Version-one and version-two witnesses remain valid with unsupported counts unknown.
Artifact verification runs before SQLite-only cached closure reuse and publication.

Projection-only numeric/Solo scoring gaps remain explicit contest findings beside
valid source interpretation. Mixed pages retain supported results. Generic parse
failures, absent adapters, new unreviewed kinds and other unsupported dispositions
do not acquire invented positive classifications. Additional explicit dispositions
and broader acceptance remain open.

## Validation

The pre-review focused run passed **227 tests in 47.54 seconds** on 2026-09-17:

```sh
.venv/bin/pytest -q \
  tests/test_event_unsupported.py tests/test_event_gaps.py \
  tests/test_event_inventory_gaps.py tests/test_event_accounting.py \
  tests/test_event_enumerations.py tests/test_event_completion_cohort.py \
  tests/build/test_unsupported_coverage.py tests/build/test_unavailable_coverage.py \
  tests/build/test_event_local_coverage.py tests/build/test_event_page_evidence.py \
  --disable-warnings --maxfail=3
```

Ruff passed on the eight affected source modules and both new test modules.
Mypy passed on the eight affected source modules. The coordinator owns formatting
and integrated validation; these focused checks are not a source freeze or a full
suite receipt. Other agents were working on distinct paths concurrently.

The new checks cover a real adapter's changed header, report evidence and request
bindings, generic/bare failures, older valid interpretation, cutoffs and bounded
searches, reviewed-policy requirements, missing/corrupt support, revocation,
generation invalidation without admission, restore of missing artifacts, legacy
unknown records, and cached closure/publication checks after extract deletion
without SQL changes. A retained numeric fixture proves that source interpretation,
supported rounds and an unsupported contest finding remain distinct.

Independent review then found that an aggregate generation's critical unknown
could be incorrectly attributed to every request in its manifest. A new regression
reproduced the false positive. The verifier now requires a single-member manifest;
aggregate unknowns stay unassessed until the report can attribute them per request.
After that repair, **64 affected tests passed in 5.35 seconds**, covering the two
new test modules and `tests/build/test_event_page_evidence.py`. Ruff and mypy passed
again on the changed verification modules. The earlier 227-test run does not
validate the later attribution repair; integrated validation remains separate.

A subsequent metadata-only review found that malformed positive proof structures
could survive a recomputed observation checksum. Tight receipt, hash, snapshot,
request, timestamp and reviewed-policy shape validation now rejects those records
without opening artifacts. Six corruption regressions were added. **70 affected
tests passed in 5.19 seconds** after this hardening, with mypy passing on the
changed verifier. Ruff passed after correcting the new test's import order.
This later run supersedes the 64-test receipt for those affected modules.

Earlier focused runs exposed stale null-count assertions and a regression that
treated loss of every usable body as unknown rather than an observed unfinished
obligation. Those failures were repaired before the passing run above. No prior
receipt is claimed to validate these new bytes.

## Remaining gates

Independent review, integrated source-bound validation, production migration and
activation rehearsal, operating observations and a subsequent acknowledged
coverage release remain separate. No owner acceptance, new-source activation,
year acceptance, production activity or publication is recorded here.

See [D-0045](../../decisions/0045-account-for-evidence-backed-unsupported-pages.md)
and the [coverage contract](../../../docs/reference/data-model.md#event-completion-coverage).
