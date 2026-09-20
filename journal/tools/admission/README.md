# Source interpretation checks tools

Review a parser's interpretation before it can replace saved evidence, and
acquire one reviewed fixture body under exact limits.

[All tools](../README.md) · [Investigations](../../investigations/collection.md) · [Evidence](../../evidence/admission/README.md)

| Script                                                   | Purpose                                                                            |
| -------------------------------------------------------- | ---------------------------------------------------------------------------------- |
| [assess_source_admission.py](assess_source_admission.py) | Run H6 source admission contracts over retained archives, entirely offline.        |
| [fixture_exception.py](fixture_exception.py)             | Exact, operator-authorized new-source fixture quarantine; no admission activation. |
| [fixture_transport.py](fixture_transport.py)             | Bounded streaming transport for one reviewed fixture manifest, without watches.    |

The exact DCN and Step Right runners built on these two modules were one-off
operations; their receipts live under [evidence](../../evidence/admission/README.md)
and their sources in history (D-0177). Build the next fixture acquisition as a
`swingset` subcommand if it will run more than once.
