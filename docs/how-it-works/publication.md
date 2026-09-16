# Building and publishing a dataset

Swingset prepares a complete proposed dataset before it changes the public
one. This proposed version is a _candidate_. The last acknowledged public
version is the _baseline_.

## Build a consistent version

The builder reads saved state and captured inputs, applies identity and
removal rules, and writes Parquet files. Parquet is the table-file format
used by the dataset. Checks cover table relationships and supporting evidence.

The term _release closure_ means the complete set of saved inputs and derived
results needed to support a release. A candidate must not silently combine
results based on incompatible inputs or revoked evidence.

## Finish publication

Publishing sends the accepted candidate to Hugging Face and records the
remote acknowledgment. If the process stops mid-publication, reconciliation
checks what actually reached the remote before starting more work.

A file on disk is not proof that a build finished successfully. Likewise,
passing local tests does not prove that a release was published. The
[status page](../status.md) keeps those claims separate.

## Go deeper

- [Build rules](../reference/build.md): inputs, checks, and output contents.
- [Publication rules](../reference/publishing.md): remote commits and recovery.
- [Data model](../reference/data-model.md): output tables and identifiers.
- Code: [builder](../../src/swingset/build/builder.py), [evidence checks](../../src/swingset/build/closure.py), and [publisher](../../src/swingset/publish/service.py).
