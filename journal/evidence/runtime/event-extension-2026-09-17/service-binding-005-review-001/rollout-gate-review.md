# Candidate 005 schema-29 rollout gate review

Review date: 2026-09-17 UTC. This is a technical recommendation for the
coordinator, not an accepted owner decision.

## Evidence at review

- Candidate 005 is implemented, fully tested and built. Its 2,536-test, Ruff,
  mypy, source-inventory and Nix build receipts are in `validation-005/` and
  `build-005/`. It is not deployed or published.
- Candidate service bindings pass independent review in
  [service-binding.json](service-binding.json). Production remains source 003,
  schema 28, held, with six ordinary units inactive.
- Packet 003 binds candidate 005 but also binds checkpoint 002 and its private
  archive acknowledgment. Its independent 41-test, Ruff, mypy and byte-for-byte
  rebuild review passed in
  [candidate005-review-001](../../schema29-successor-rehearsals-2026-09-17/candidate005-review-001/review-002/checks.json).
- The coordinator reports that packet 003's actual migration rehearsal passed:
  all 116 predecessor tables were unchanged, one new table was created, and
  integrity and foreign-key checks passed. The separate actual restore is
  running. Packet 003's checkpoint cutoff records 15 Archive requests; current
  retained usage is 20 requests / 2,952,065 bytes.

## Recommended gate before live migration

1. Finish any already-authorized, independently reviewed origin fixture work.
   Once paid acquisition for this rollout is done, capture and verify a new
   schema-28 checkpoint under the currently deployed source and hold. Confirm
   its input bundle, baseline, all 116 tables, request/budget/spacing controls,
   current Archive usage, and hold. Acknowledge its exact manifest in the
   private archive and verify the returned head and manifest.
2. Create a new packet attempt binding candidate 005 to that checkpoint and
   archive receipt. Packet 003 must remain evidence for its 15-request cutoff.
   Independently review the new packet's deterministic build and full helper
   closure before running it. It must keep migration, restore and input replay
   separate.
3. Restore the fresh checkpoint to a new disposable held state. Preserve all
   116 predecessor tables and all controls. Allow only the restore protocol's
   singleton `event_pressure_state.epoch += 1`; verify `sequence` and all other
   data are unchanged, the public baseline checks pass, restore completes, the
   hold remains, `RESTORE_PENDING` clears, and no source request, input
   acceptance, repair activation or public write occurs.
4. Migrate a separate database-only copy of that same checkpoint with the
   candidate 005 helper. Verify schema markers reach 29, all 116 prior table
   receipts remain unchanged, integrity and foreign keys pass, reopening is
   stable, and the checkpoint, hold, baseline, accepted inputs, paid usage and
   request history still match their pinned receipts.
5. On a third disposable copy, run the sealed scratch phases: prepare (including
   schema-29 migration), accept the exact frozen config and six overrides, then
   a bounded offline drain. Pin the starting requirement cohort and report
   offered, served, failed, unfinished and newly discovered work separately.
   Measure selector latency and per-unit throughput against the source-28
   baseline with the same recorded cohort and budgets; a read-only profile is
   not sustained service. Enforce no network, publication, repair activation or
   production input acceptance. Do not infer fleet completeness from an empty
   queue or a short selection window.
6. Only after those receipts pass, recheck the unchanged production hold,
   source 003/system 003, schema 28, baseline, input bundle, paid usage and
   inactive units. Deploy candidate 005 under the hold, recheck the new system
   and all unit bindings, then run a dedicated source-005 schema-28-to-29 live
   migration gate. Post-migration checks must again account for all 116
   predecessor tables, schema/integrity state, controls, hold, baseline and
   service inactivity. Keep a rollback path to the fresh checkpoint and the
   compatible source-003 system.

`accept_event_extension.py` is not an eligible driver: it is pinned to the
schema-14-to-28 transition and its old source/system receipts. Do not make it
generic by changing command-line arguments. Use a separately reviewed
schema-28-to-29 live gate tied to candidate 005 and the fresh checkpoint.

## Boundaries

This review does not authorize year acceptance, input acceptance, ordinary job
resumption, source-kind activation or publication. Deployment and publication
remain authorized by the recorded owner decisions once their concrete gates
pass. H17 still has zero human adjudications; V5 and V6 remain open.
