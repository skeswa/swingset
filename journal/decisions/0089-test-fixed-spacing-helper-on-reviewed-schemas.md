# D-0089: Test the fixed spacing helper on its reviewed schemas

Recorded: 2026-09-17  
Decided by: agent  
Topic: Source-bound validation  
Supersedes: —  
Superseded by: —

## Decision

Open schema 28 explicitly in tests of the legacy spacing-baseline operation
helper. Keep the existing schema-14 preparation scenario. Add a schema-29
rejection check that proves the helper makes no changes on an unsupported
schema. Do not widen the helper's reviewed production authority.

## Why

Candidate 004's full run passed 2,525 tests and failed ten. Every failure came
from these fixtures following the runtime default to schema 29, while the
operation helper correctly requires schemas 14 or 28. Ruff and mypy passed.
This is a test fixture correction; no runtime or helper behavior changes.

Keep the failed run and source immutable. Freeze candidate 005 as the exact
candidate-004 inventory with only this test file changed, then repeat full
validation. Later operation packets must bind the new exact source receipt;
the runtime-byte equality proof does not rename earlier receipts.
