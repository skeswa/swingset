# Worker state and recovery tools

Check migrations, controls, scheduling, checkpoints, and isolated replay.

[All tools](../README.md) · [Investigations](../../investigations/runtime.md) · [Evidence](../../evidence/runtime/README.md)

| Script                                                                           | Purpose                                                                                      |
| -------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------- |
| [accept_h11.py](accept_h11.py)                                                   | Guarded H11 migration and shadow-inventory acceptance; never deploy or fetch.                |
| [accept_h12.py](accept_h12.py)                                                   | Offline H12 schema 10→11 preflight; --execute only migrates and verifies.                    |
| [accept_h13.py](accept_h13.py)                                                   | Offline H13 schema 11→12 preflight; --execute only migrates and verifies.                    |
| [accept_h14.py](accept_h14.py)                                                   | Bounded H14 schema 12→13 migration acceptance; no service or source execution.               |
| [accept_h15.py](accept_h15.py)                                                   | Bounded H15 schema 13→14 migration acceptance; no service or source execution.               |
| [accept_h16.py](accept_h16.py)                                                   | Bounded H16 schema-14 acceptance: read-only state, no migration or service.                  |
| [accept_held_schema29_inputs.py](accept_held_schema29_inputs.py)                 | Seal and accept the exact candidate-006 inputs under hold; cannot resume or publish.         |
| [accept_wp16.py](accept_wp16.py)                                                 | Reviewed WP16 schema14→15 migration only; preflight is read-only by default.                 |
| [assemble_h11_source.py](assemble_h11_source.py)                                 | Prepare a selective H11 tree from the acknowledged V4 source pin; never deploy.              |
| [audit_checkpoint_generation_closure.py](audit_checkpoint_generation_closure.py) | Read-only audit of generation artifact references against a private checkpoint.              |
| [benchmark_fk_indexes.py](benchmark_fk_indexes.py)                               | Audit FK lookup plans and time rolled-back deletes on a disposable copy.                     |
| [benchmark_identity_references.py](benchmark_identity_references.py)             | Measure retained source-reference coverage without migrating or changing state.              |
| [benchmark_requirements.py](benchmark_requirements.py)                           | Time two inventory scans on an explicitly disposable state copy, offline.                    |
| [h14_picker_cost.py](h14_picker_cost.py)                                         | Read-only retained-demand picker timing; older schemas use empty TEMP counters.              |
| [h14_shadow_load.py](h14_shadow_load.py)                                         | Read one retained SQLite snapshot and emit H14 load evidence; never import runtime.          |
| [measure_state_storage.py](measure_state_storage.py)                             | Read-only per-table, per-stage and payload-digest storage report on a disposable copy.       |
| [replay_derivations.py](replay_derivations.py)                                   | Resume real project/link workers on a marker-bound SQLite scratch copy only.                 |
| [checkpoint_h16_20260917.py](checkpoint_h16_20260917.py)                         | Capture and verify the published H16 checkpoint with the pinned deployed runtime.            |
| [rehearse_extension_migration.py](rehearse_extension_migration.py)               | Verify current-schema migration on a disposable copy of a source-bound schema-14 checkpoint. |
| [repair_h16_backup_read_access.py](repair_h16_backup_read_access.py)             | One-time, exact-path H16 receipt permission repair; refuses changed preconditions.           |

The [operational restore rehearsal](rehearse_extension_restore.py) restores a
pinned checkpoint into disposable held state, verifies the actual public baseline
and private evidence, then migrates under the hold. It accepts no runtime inputs.

The [current schema-29 packet builder](prepare_current_schema29_rehearsals.py)
binds candidate 005 to a newly acknowledged held schema-28 checkpoint, including
paid requests and accounting sidecars. Its migration, restore and input replay
are separate disposable operations. The [held migration helper](migrate_held_schema29.py)
requires their closed evidence before a live schema-only change; it accepts no
production inputs and resumes no services. See the
[successor record](../../investigations/2026/schema29-successor-rehearsals-2026-09-17.md)
for exact reviewed attempts and current operating limits.

The [held schema-29 input helper](accept_held_schema29_inputs.py) keeps input
acceptance separate from migration and ordinary operation. Its read-only
preflight, disk-backed seal and reviewed-seal execution bind candidate 006, the
postmigration audit, checkpoint-004 packet and rehearsed 51-row input map. The
focused 17-test suite, Ruff and mypy pass. Its distinct preflight, disk-backed
seal and execution gates passed independent review, and the sealed production
transition passed under the hold. It started no workers and performed no fetch,
repair or publication.

The [schema-29 timing diagnostic](measure_schema29_overhead.py) compares rolled-back
updates on a disposable database copy. It isolates the new triggers from older
triggers; its measurements do not establish whole-worker throughput.

The [state storage measurement](measure_state_storage.py) answers step 1 of the
bounded state plan: bytes per table and index from `dbstat`, file size against
bytes in use and free-list bytes, and distinct payload digests against total
rows per stage, and declared row counts against the rows that read back. It
opens the database `mode=ro&immutable=1`, so it creates no sidecar files and
cannot write to what it measures, and it refuses `/var/lib/swingset` and any
database a connection still holds open. Every path it writes to is fenced the
same way and checked before the measurement starts: not under
`/var/lib/swingset`, not inside the measured copy, and not already there. It
exits non-zero when a gate fails.
`--time-backup` times a restore, a checkpoint and its verification into a
scratch directory it is given; on a copied held checkpoint it restores first and
backs up the restored tree. The measurement itself has not been run; see the
[investigation](../../investigations/2026/state-storage-measurement-2026-09-18.md).
