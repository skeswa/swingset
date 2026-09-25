# Builds and releases tools

Check a built candidate against its supporting evidence, or against another
candidate.

[All tools](../README.md) · [Investigations](../../investigations/releases.md) · [Evidence](../../evidence/releases/README.md)

| Script                                         | Purpose                                                                                                                  |
| ---------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------ |
| [audit_candidate.py](audit_candidate.py)       | Audit one built dataset candidate without network access.                                                                |
| [compare_candidates.py](compare_candidates.py) | Compare two candidates' rows by key, provenance columns left out; the phase 1 deploy runs it before and after the drain. |

The V1, V3 and V4 correction and acceptance runners were one-off operations;
their receipts live under [evidence](../../evidence/releases/README.md) and
their sources in history (D-0177).
