# Separating identity resolution from storage

Date: 2026-09-17  
Type: Outcome  
Topic: Identity linking  
Related decision: [D-0042](../../decisions/0042-separate-link-evidence-resolution-and-persistence.md)

## Summary

The linker now loads retained evidence, resolves identities without storage
access, and commits the result against the selected input versions. The public
operation remains `link_event`. This is a local refactor; it has not been
deployed or published.

## Findings

The former `link_event` function combined evidence loading, identity policy,
assignment, SQL writes, history, and scheduling. Its nested writer chose the
final identity. The extracted `resolve_event` returns subject evidence,
candidate assessments, reviewed restrictions, conclusions, and findings.

Confirmed, tentative, unmatched, and withheld conclusions are explicit internal
types. Only confirmed conclusions provide a default-join identity. Stored link
statuses, candidate scores and signals, history reasons, input guards, work
completion, placement updates, and confirmation cadence retain their existing
behavior. The shared review policy remains available to publication through
`DecisionResolver`.

The reference previously described event-wide assignment and weighted surname
rarity. The code assigns within each contest and role and records rarity without
using it in the score. Documentation now describes those implemented rules and
marks unavailable geography and unreconstructed historical division levels.

## Validation

- Before extraction, 132 focused linking, correction, and publication-policy
  tests passed.
- After extraction, [195 focused tests passed](../../evidence/identity/link-refactor-2026-09-17/focused-tests.log).
  These include the pure resolution examples, candidate pools, dependency
  selection, corrections, and publication policy. The concurrency test still
  checks that newly accepted decisions cannot be overwritten or lose relink work.
- A [comparison against the original implementation](../../evidence/identity/link-refactor-2026-09-17/equivalence.log)
  passed 107 tests and compared 37 link calls across 12 persisted tables. It
  compares links, candidates, entries, judges, placements, findings, current
  resolutions, history, reference bindings, watches, revisions, and pending work.
  Only SQLite's wall-clock timestamp on automatically superseded history rows
  is excluded; assertion and resolution content remain compared. The concurrency
  test uses its existing assertions rather than the comparison wrapper.
- `mise run fmt`, `ruff check .`, and strict `mypy` passed. Formatting changed
  no unrelated files; mypy checked 212 source files.

The full offline suite passed: **2,088 tests in 445.69 seconds**. These checks
exercise synthetic and retained offline fixtures. They do not establish population precision or production performance.

## Evidence and reproduction

The original service and decision resolver are pinned to commit
`b1b5b856bd6e8bae80555dd9f51b6b3e78ca10f2`. The
[frozen comparison plugin](../../evidence/identity/link-refactor-2026-09-17/link_equivalence_plugin.py)
reads those files through `jj file show` and checks their SHA-256 hashes before
loading them. It runs the original implementation inside a rolled-back
savepoint, then runs the refactored implementation on the same starting state.

From the repository root, with the locked development environment installed:

```sh
PYTHONPATH=journal/evidence/identity/link-refactor-2026-09-17:src:tests \
  .venv/bin/python -m pytest -q -p link_equivalence_plugin \
  tests/test_link_service.py tests/test_link_pools.py \
  tests/test_identity_decisions.py tests/test_identity_corrections.py
.venv/bin/python -m pytest -q tests/test_link_resolution.py tests/test_link.py \
  tests/test_link_service.py tests/test_link_pools.py tests/test_link_invalidation.py \
  tests/test_link_dependency_selection_scale.py tests/test_identity_decisions.py \
  tests/test_identity_corrections.py tests/build/test_identity_policy.py
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check .
.venv/bin/python -m mypy
mise run fmt
```

The existing local virtual environment supplied the locked tools; `uv` was not
on this shell's path. No live-source requests were made.

## Reading the implementation

Start with [the public operation](../../../src/swingset/link/service.py),
then [the event workflow and precedence](../../../src/swingset/link/resolution.py)
and [the result types](../../../src/swingset/link/model.py). The
[identity reference](../../../docs/reference/identity-linking.md#implementation-and-inspection)
maps the remaining storage and policy responsibilities.
