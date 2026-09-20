# Worker state and recovery tools

Measure state storage, benchmark layouts on a disposable copy, and replay
derivations on scratch. None of these touch `/var/lib/swingset`.

[All tools](../README.md) · [Investigations](../../investigations/runtime.md) · [Evidence](../../evidence/runtime/README.md)

| Script                                                                                   | Purpose                                                                                   |
| ---------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------- |
| [measure_state_storage.py](measure_state_storage.py)                                     | Read-only table, index, source-JSON and derivation storage report on a disposable copy.   |
| [profile_source_generation_json.py](profile_source_generation_json.py)                   | Bounded, aggregate-only source-generation JSON structure profile on a disposable copy.    |
| [benchmark_derivation_row_layout.py](benchmark_derivation_row_layout.py)                 | Compare rowid and WITHOUT ROWID derivation rows on compact full schema-29 copies.         |
| [benchmark_source_generation_compression.py](benchmark_source_generation_compression.py) | Compare plain and independently compressed source-generation JSON on compact full copies. |
| [replay_derivations.py](replay_derivations.py)                                           | Resume real project/link workers on a marker-bound SQLite scratch copy only.              |

The [state storage measurement](measure_state_storage.py) answers step 1 of the
[bounded state plan](../../../docs/plans/bounded-state-and-archive.md): bytes
per table and index from `dbstat`, file size against bytes in use and free-list
bytes, distinct payload digests against total rows per stage, and declared row
counts against the rows that read back. It opens the database
`mode=ro&immutable=1`, refuses `/var/lib/swingset` and any database a
connection still holds open, fences every path it writes, and exits non-zero
when a gate fails. `--time-backup` times a restore, a checkpoint and its
verification into a scratch directory. The two benchmarks and the JSON profile
share those fences. See the
[investigation](../../investigations/2026/state-storage-measurement-2026-09-18.md).

The H11 to H16 acceptance helpers, schema-29 rehearsal, migration, checkpoint
and input-acceptance helpers, spacing baselines and selector profiles were
one-off operations; their receipts live under
[evidence](../../evidence/runtime/README.md) and their sources in history (D-0177).
