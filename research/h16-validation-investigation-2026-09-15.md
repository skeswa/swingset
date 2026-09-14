# H16 validation investigation

Reviewed 2026-09-15 America/Denver. The fresh VM diagnostic ran on
2026-09-16 UTC. Investigation only: no runtime fix, build retry, deployment,
production initialization, or publication.

## Findings

The release blocker is excessive work inside the 45-second completion
transaction. The retained failure log shows interruption during the third
full closure validation, inside source-support reconstruction. The failure
was not the memory monitor stopping the process. The transaction rolled back;
candidate files alone remain insufficient for publication.

The fresh read-only diagnostic used the exact failed release source
`2d5q3lgljfkmm78hf44g954lzwx79yhv-source` and failed-build database at
`/var/tmp/swingset-h16-closure-build`. It validated the actual candidate's
retained closure, with 34,986 selected generations, 68,380 dependency sets,
and 58,169 source-support records.

| Check                                      | Result                |
| ------------------------------------------ | --------------------- |
| First full validation                      | Passed, 15.14 seconds |
| Second full validation                     | Passed, 10.30 seconds |
| Third full validation                      | Passed, 9.34 seconds  |
| Three unprofiled validations combined      | 34.78 seconds         |
| Additional profiled validation             | Passed, 12.26 seconds |
| Database writes                            | Zero                  |
| Database and sidecar size/mtime comparison | Unchanged             |
| Peak diagnostic process RSS                | About 1.57 GiB        |

Later passes were faster. This makes a single isolated timing an unreliable
predictor of full-build completion. The combined 34.78 seconds excludes proof
retention, repeated derivation fingerprint construction, output insertion,
dependency checks, and certification bookkeeping. It also excludes the
memory and I/O conditions left by generating the full candidate. The earlier
failed build's process maximum RSS was about 4.61 GiB.

These checks found no closure-validation rejection in the retained state.
They do not certify all candidate data, prove full-build performance, or
replace release acceptance.

## Where the time goes

The fresh profile identifies these cumulative costs within one validation:

| Work                                         | Seconds |
| -------------------------------------------- | ------- |
| Reconstruct source support                   | 5.61    |
| Traverse selected generation graph           | 3.57    |
| Recheck support receipts and snapshot hashes | 1.87    |

One pass made 223,716 SQLite `execute` calls and 190,614 `json.loads` calls.
JSON decoding accounts for 4.21 cumulative seconds, overlapping the work in
the table. SQL execution itself accounts for 1.11 seconds in this warmed
profile. The problem includes substantial Python decoding and reconstruction;
it is not established to be one slow SQL query or a missing index.

The call sequence is confirmed in
[derivations.py](../src/swingset/state/derivations.py): `complete()` calls
`desired()` before output and after output rows, then `_certify()` calls it
again. For a build with a pinned closure, each `desired()` calls the complete
[closure validator](../src/swingset/build/closure.py). Each validation rebuilds
the graph and source-support witnesses. The existing regression test explicitly
requires all three validation boundaries.

The next performance change should reduce repeated reconstruction while
preserving those safety boundaries. Candidate approaches include sharing
verified immutable evidence within one completion operation and combining
duplicate support-receipt reads. Any reuse needs an explicit validity rule:
mutable admission, revocation, snapshot, policy, and baseline state must still
be checked at the required boundaries. Source and derivation immutability
triggers are useful evidence, but do not by themselves justify caching every
validation result. No such optimization was implemented here.

Acceptance must include a fresh full build under representative memory and I/O
conditions, with the unchanged 45-second bound. Another successful isolated
completion would not resolve the release blocker.

## Separate development-check issues

Ruff passes. The combined H16 acceptance and focused regression selection passes
39 tests in 18.24 seconds:

```sh
.venv/bin/pytest tests/test_h16_acceptance.py tests/build/test_closure_graph_cost.py tests/build/test_closure_support_queries.py tests/build/test_completion_validation.py -q
```

Running the completion tests without a top-level test module fails collection
with `ModuleNotFoundError: No module named 'test_h16_acceptance'`. The tests
import a fixture from that sibling module, but pytest configuration only adds
`src` to its explicit Python path. Collecting the top-level test module masks
the problem. This configuration-only override confirms the cause; all four
completion tests pass in 2.75 seconds:

```sh
.venv/bin/pytest -o 'pythonpath=src tests' tests/build/test_completion_validation.py -q
```

A small follow-up is to declare `tests` in pytest's path configuration, or move
the shared fixture into an explicitly importable support module. No test-path
change was applied during this investigation.

Mypy checks 172 source files and reports one error at
[scoringdance/adapter.py](../src/swingset/sources/scoringdance/adapter.py):231:
the chained header-attributes `.get()` call has an inferred
`Collection[str] | Any` receiver. The locally constructed cell combines text
and an attributes dictionary, and its inferred value type is too broad for
that chained access. A typed cell structure or explicit dictionary narrowing
would resolve it. This is a working-copy parser issue; it is not the cause of
the frozen H16 completion timeout. No parser behavior was changed here.

## Evidence and reproduction

- [Fresh read-only measurements and profile](verification/h16-validation-investigation-20260915.json), SHA-256 `b8fe7151df1509366e45a803dee9aa05bddd7aaaa1a5ce3278e8efe2bcb388e1`.
- [Exact diagnostic script](verification/h16-validation-investigation-20260915.py). It takes the existing scratch writer lock, requires an empty WAL, opens SQLite read-only with immutable mode, checks source and candidate hashes, and bounds the diagnostic at 180 seconds. It refuses to overwrite its report.
- [Original failed-build journal](verification/h16-closure-build-journal-20260915.log).
- [Original build receipt](verification/h16-closure-build-20260915.json).

The VM venv requires the native-library path supplied by `nix/module.nix`.
The first manual diagnostic invocation omitted it and failed importing PyArrow
with `libstdc++.so.6` missing, before opening the database. Supplying the same
GCC/zlib library paths as the service resolved that invocation issue.
It was not a failure of the installed service configuration.
