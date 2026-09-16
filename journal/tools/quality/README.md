# Dataset quality tools

Inspect a saved dataset and explain missing or conflicting results.

[All tools](../README.md) · [Investigations](../../investigations/quality.md) · [Evidence](../../evidence/quality/README.md)

| Script                                                   | Purpose                                                                                  |
| -------------------------------------------------------- | ---------------------------------------------------------------------------------------- |
| [capture_missing_data.py](capture_missing_data.py)       | Capture a consistent SQLite backup and pin the published candidate for an offline audit. |
| [missing_data_archive.py](missing_data_archive.py)       | Verify artifacts referenced by a captured state against a local archive.                 |
| [missing_data_details.py](missing_data_details.py)       | Audit identity and result-detail completeness in a pinned published candidate.           |
| [missing_data_events.py](missing_data_events.py)         | Audit published event/result coverage against captured discovery state.                  |
| [missing_data_fields.py](missing_data_fields.py)         | Profile null and blank fields in a pinned publication; absence is not always a defect.   |
| [missing_data_operations.py](missing_data_operations.py) | Summarize intake gaps from a captured SQLite state without fetching sources.             |
| [missing_data_registry.py](missing_data_registry.py)     | Reproduce the 2026-09-12 offline registry gap audit.                                     |
| [self_healing_checks.py](self_healing_checks.py)         | Reproduce self-healing defects offline, using temporary state and mock HTTP.             |
