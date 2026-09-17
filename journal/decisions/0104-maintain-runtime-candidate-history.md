# D-0104: Maintain runtime candidate history

Status: Accepted  
Recorded: 2026-09-17  
Accepted: 2026-09-17  
Acceptance source: Owner instruction in the Codex session to add a candidate-difference document under `docs/reference`.  
Topic: Runtime documentation  
Supersedes: —  
Superseded by: —

## Decision

Maintain `docs/reference/candidate-history.md` as the readable index of frozen
runtime candidates. Record substantive changes from the predecessor, test-only
changes, validation results, and deployment and publication separately. Link
exact inventories and receipts. Keep the current live state in `docs/status.md`.

## Why

Candidate differences were scattered across long investigation journals and
machine-readable inventories. The owner asked for one reference document.
Explicitly separating source candidates, schema versions and dataset candidates
also prevents a successful source test or rehearsal from implying deployment,
publication or historical-year acceptance.

This acceptance concerns the documentation only. It grants no new operating,
source-kind, year-review or identity-adjudication acceptance.

## Links

- [Candidate history](../../docs/reference/candidate-history.md)
- [Reference index](../../docs/reference/README.md)
- [Current status](../../docs/status.md)
