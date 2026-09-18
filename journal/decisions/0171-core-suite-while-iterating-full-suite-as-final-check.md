# D-0171: Run the core suite while iterating and the full suite as a final check

Recorded: 2026-09-18  
Decided by: owner, 2026-09-18; source: session instruction "update internal guidance to prefer core tests only unless it is a final/important check"  
Topic: Development checks  
Supersedes: —  
Superseded by: —

## Decision

Agents and people run the reviewed core suite and the test files they changed
while iterating. `--full-suite` is for a final or important check only: before
a commit or handoff, or when a change touches crash recovery, migrations,
backup and restore, publication, or another area the extended suite covers.
AGENTS.md, the development guide and the testing guide say so.

## Why

A full run takes about ten minutes and writes about 15 GiB of temp; a core run
takes about three minutes. On 2026-09-18 agent sessions ran the full suite
every 10 to 15 minutes for eleven hours, which cost hours of waiting and, with
the temp leak of [D-0170](0170-pin-tmpdir-in-the-dev-shell-so-test-temp-is-pruned.md),
filled the disk. The core suite exists so that routine checks are fast
([D-0168](0168-run-core-tests-by-default-and-keep-an-extended-suite.md)).

## Alternatives

- Always run the full suite. Rejected: it is the cost this rule removes.
- Never run it locally and rely on CI. Rejected: the extended cases cover
  crash boundaries and migrations that a final local check should exercise.

## Consequences

A change can pass its iteration checks and still fail the final full run;
that is accepted, and the final run stays mandatory before a commit.

## Links

- [Testing guide](../../docs/guides/testing.md)
- [Repository instructions](../../AGENTS.md)
