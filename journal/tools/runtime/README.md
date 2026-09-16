# Worker state and recovery tools

Check migrations, controls, scheduling, checkpoints, and isolated replay.

[All tools](../README.md) · [Investigations](../../investigations/runtime.md) · [Evidence](../../evidence/runtime/README.md)

| Script                                                                           | Purpose                                                                             |
| -------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------- |
| [accept_h11.py](accept_h11.py)                                                   | Guarded H11 migration and shadow-inventory acceptance; never deploy or fetch.       |
| [accept_h12.py](accept_h12.py)                                                   | Offline H12 schema 10→11 preflight; --execute only migrates and verifies.           |
| [accept_h13.py](accept_h13.py)                                                   | Offline H13 schema 11→12 preflight; --execute only migrates and verifies.           |
| [accept_h14.py](accept_h14.py)                                                   | Bounded H14 schema 12→13 migration acceptance; no service or source execution.      |
| [accept_h15.py](accept_h15.py)                                                   | Bounded H15 schema 13→14 migration acceptance; no service or source execution.      |
| [accept_h16.py](accept_h16.py)                                                   | Bounded H16 schema-14 acceptance: read-only state, no migration or service.         |
| [accept_wp16.py](accept_wp16.py)                                                 | Reviewed WP16 schema14→15 migration only; preflight is read-only by default.        |
| [assemble_h11_source.py](assemble_h11_source.py)                                 | Prepare a selective H11 tree from the acknowledged V4 source pin; never deploy.     |
| [audit_checkpoint_generation_closure.py](audit_checkpoint_generation_closure.py) | Read-only audit of generation artifact references against a private checkpoint.     |
| [benchmark_fk_indexes.py](benchmark_fk_indexes.py)                               | Audit FK lookup plans and time rolled-back deletes on a disposable copy.            |
| [benchmark_identity_references.py](benchmark_identity_references.py)             | Measure retained source-reference coverage without migrating or changing state.     |
| [benchmark_requirements.py](benchmark_requirements.py)                           | Time two inventory scans on an explicitly disposable state copy, offline.           |
| [h14_picker_cost.py](h14_picker_cost.py)                                         | Read-only retained-demand picker timing; older schemas use empty TEMP counters.     |
| [h14_shadow_load.py](h14_shadow_load.py)                                         | Read one retained SQLite snapshot and emit H14 load evidence; never import runtime. |
| [replay_derivations.py](replay_derivations.py)                                   | Resume real project/link workers on a marker-bound SQLite scratch copy only.        |
