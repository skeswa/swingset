# Focused tests and source type checking pass independently

Date: 2026-09-16, America/New_York  
Type: Outcome  
Topic: Development checks  
Related plan or decision: [D-0008](../../decisions/0008-local-validation-checks.md), [history and recovery plan](../../../docs/plans/history-and-recovery.md)

## Summary

The two local development-check failures identified in the
[validation investigation](h16-validation-investigation-2026-09-15.md) are fixed.
The completion test module now runs alone. Mypy passes across all 172 source
files. These checks do not establish that the full release build meets its
completion deadline.

## Findings

Before the changes, running the completion test module alone failed collection
with `ModuleNotFoundError: No module named 'test_h16_acceptance'`. Adding `tests`
to the pytest import path lets it find the existing shared fixture without
depending on collection of another test module.

Mypy independently reproduced the scoring.dance header error: its inferred
attributes value could be a `Collection[str]`, which has no `.get()` method.
An explicit dictionary check before reading the title resolves the error.
All attributes values reaching this branch are constructed locally as
dictionaries, so the added check does not change extracted results. The round
extractor and parser versions remain 4.

The changes are limited to pytest configuration and the header type check.
Existing tests cover the affected release completion boundaries and source
behavior; no new test duplicates the implementation.

## Evidence and reproduction

Run from the repository root using the existing project environment:

| Command                                                                                                                               | Result                        |
| ------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------- |
| `.venv/bin/pytest -q tests/build/test_completion_validation.py`                                                                       | 4 passed in 5.63 seconds      |
| `.venv/bin/pytest -q tests/test_sources.py tests/test_scoring_pipeline.py tests/test_callback_quality.py tests/test_event_quality.py` | 21 passed in 6.50 seconds     |
| `.venv/bin/mypy src/swingset`                                                                                                         | No issues in 172 source files |
| `.venv/bin/ruff check src/swingset/sources/scoringdance/adapter.py tests/build/test_completion_validation.py`                         | Passed                        |

These results describe the September 16 working copy, including the two edits
in [pytest configuration](../../../pyproject.toml) and the
[scoring.dance adapter](../../../src/swingset/sources/scoringdance/adapter.py).
They are local checks, with no deployment or publication.

## Conclusion and follow-up

Focused completion tests and source type checking can now be used directly
while implementing the validation performance fix. The representative full
build and its release acceptance checks remain separate work.
