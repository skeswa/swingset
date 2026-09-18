# Restore activation skipped admissions before schema 19

Date: 2026-09-18. Purpose: record a local restore-activation recovery fix.
Authority: [D-0018](../../decisions/0018-gate-expansion-with-conservative-observations.md).

## Bug

`execution_admissions` exists from schema 12, but restore activation used the
schema-19 `event_pressure_state` table as the condition for both admission
recovery and event-pressure invalidation. A restored schema 12 through 18
therefore removed `RESTORE_PENDING` while abandoned local, request or
publication admissions could remain active.

## Reproduction

A schema-12 database with active local and publication admissions has no
`event_pressure_state` table. Under the old condition, activation skipped its
transaction, left both admissions active, and removed the restore marker. A
schema-11 database has neither table. Schema 19 and later have both tables and
followed the intended path.

## Fix

Activation now checks the two tables independently under the existing
exclusive restore locks. If `execution_admissions` exists, it recovers
abandoned work: local and request actions settle as `process_interrupted`, and
publication becomes uncertain with `receipt_reconciliation_required`. If
`event_pressure_state` exists, activation also increments its epoch. Both
changes share one immediate transaction when both tables exist, and the marker
is removed only after commit.

## Validation

Three historical-schema regressions cover schema 11 compatibility, schema 12
admission recovery while the marker still exists, and schema 19 admission
recovery plus one epoch increment. The full checkpoint test file passed 20
tests. The checkpoint, durable-control and event-completion restore files passed
47 tests together. The final combined working copy passed all 3,034 offline
tests in 573.86 seconds, Ruff, and mypy over 232 source files. Formatting
passed. The full run used the documented empty-`PYTHONPATH` correction for the
local subprocess environment.

## Boundary

This is local source and test work. It has not been built into a candidate,
deployed, exercised against production state, or used for a production restore.
