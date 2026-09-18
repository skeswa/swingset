# D-0023: Include build tests in default discovery

Recorded: 2026-09-16  
Decided by: agent  
Topic: Test discovery  
Supersedes: —  
Superseded by: —

## Decision

Set pytest's recursion exclusions explicitly so ordinary discovery includes
`tests/build`. Continue excluding hidden directories, bytecode caches, and
fixtures. Keep the existing test roots and import paths.

## Why

Pytest's default recursion exclusions contain `build`. Earlier default suite
counts therefore omitted that directory. Focused build suites were run
separately, but those runs do not establish that every build test passed with
the latest combined source. Preserve their receipts and record this limitation.

## Consequences

The next combined run must include build tests. Its source-bound receipt and
collection count supersede earlier default-discovery counts for current local
validation. Frozen release validation must also include this directory, either
through this setting or an explicit test path. This changes no runtime behavior.
