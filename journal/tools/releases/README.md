# Builds and releases tools

Check candidates and correction releases against their supporting evidence.

[All tools](../README.md) · [Investigations](../../investigations/releases.md) · [Evidence](../../evidence/releases/README.md)

| Script                                                     | Purpose                                                                              |
| ---------------------------------------------------------- | ------------------------------------------------------------------------------------ |
| [audit_candidate.py](audit_candidate.py)                   | Audit one built dataset candidate without network access.                            |
| [build_v1_correction.py](build_v1_correction.py)           | Build the V1 identity correction under an explicit baseline restriction.             |
| [v1_correction_acceptance.py](v1_correction_acceptance.py) | Read-only V1 correction audit; never publishes or changes pipeline state.            |
| [v3_correction_acceptance.py](v3_correction_acceptance.py) | Verify a correction-only candidate against its acknowledged published baseline.      |
| [v4_candidate_acceptance.py](v4_candidate_acceptance.py)   | Read-only V2/V4 candidate acceptance against an acknowledged V3 baseline.            |
| [v4_history_checks.py](v4_history_checks.py)               | Independent, read-only V2 checks for the combined V2/V4 candidate audit.             |
| [v4_identity_checks.py](v4_identity_checks.py)             | Read-only identity checks over old_TABLE/new_TABLE release views and retained state. |
