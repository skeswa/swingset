# D-0168: Run core tests by default and keep an extended suite

Status: Accepted  
Recorded: 2026-09-18  
Accepted: 2026-09-18  
Acceptance source: The owner requested high-impact tests running in under five minutes on this hardware, then selected “Keep an opt-in extended suite (recommended).”  
Topic: Test suite maintenance  
Supersedes: —  
Superseded by: —

## Decision

Make the reviewed core the default pytest and CI selection. Retain specialist
tests behind `--full-suite`; explicit file or node requests still run the
requested tests. Keep roughly 1,000 high-impact core cases, with a measured
serial target below five minutes on the development Mac.

Use one checked selection file. New test files and stale core selectors fail
default collection until classified. Keep actual source fixtures, destructive
state/recovery checks, publication/identity safety and pipeline integrations.

Replace routine exhaustive crash injection with before/after checks at ten
distinct durable transitions. Keep every other transaction boundary in the
extended run. Verify recovered event facts, unfinished work and completed
candidate files. Do not add fault hooks to runtime code.

## Why

The [initial measurement](../investigations/2026/pytest-suite-review-2026-09-18.md#follow-up-measured-runtime-on-2026-09-18)
took 10 minutes 37 seconds. One exhaustive crash test consumed 4 minutes
24 seconds. Counting that loop as one test hid its 364 crash/restart child runs.
The owner accepted reduced routine selection while retaining extended coverage;
this implements that option from [D-0165](0165-reduce-tests-by-behavior-and-tool-lifetime.md).

## Consequences

The routine suite trades exhaustive combinations and historical tool checks for
faster feedback. The default passing result does not certify the extended suite.
No excluded tests or retained evidence are deleted. Test count may grow as core
files gain cases; selection and runtime need review when they change materially.
The [testing guide](../../docs/guides/testing.md) owns commands and selection rules.
