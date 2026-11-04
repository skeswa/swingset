# Source interpretation checks tools

Review a parser's interpretation before it can replace saved evidence, and
acquire one reviewed fixture body under exact limits.

[All tools](../README.md) · [Investigations](../../investigations/collection.md) · [Evidence](../../evidence/admission/README.md)

| Script                                                   | Purpose                                                                     |
| -------------------------------------------------------- | --------------------------------------------------------------------------- |
| [assess_source_admission.py](assess_source_admission.py) | Run H6 source admission contracts over retained archives, entirely offline. |
| [fixture_exception.py](fixture_exception.py)             | Historical SQLite quarantine for its exact approved manifest; retired.      |
| [fixture_transport.py](fixture_transport.py)             | Historical bounded transport for that retired fixture exception.            |

The exact DCN and Step Right runners built on these two modules were one-off
operations; their receipts live under [evidence](../../evidence/admission/README.md)
and their sources in history (D-0177). Build the next fixture acquisition as a
`swingset` subcommand if it will run more than once.

These fixed-manifest helpers do not support current Postgres state. Their
27 original case nodes, including real SQLite authorizer, writer-lock and
process-crash checks, run against the [frozen original source](../../../tests/fixtures/legacy_sqlite37/README.md).
Current acquisition and database guards run natively in
[fetch](../../../tests/test_fetch.py), [paid pacing](../../../tests/test_paid_pacing.py),
[controls](../../../tests/test_controls.py) and [Postgres engine](../../../tests/test_postgres_engine.py) tests.
