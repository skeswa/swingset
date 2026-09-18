# D-0030: Retain live operation evidence outside workspaces

Recorded: 2026-09-16  
Decided by: agent  
Topic: Operation evidence  
Supersedes: —  
Superseded by: —

## Decision

Write active operation logs and samples to private VM-local files, then copy
closed evidence into the journal. Avoid version-control operations that replace
paths while a process still writes through an open descriptor.

## Why

The H16 replay sample pathname was replaced while its supervisor kept writing
to the deleted file. The exact cause is unconfirmed; workspace updates occurred
during the replay. Detailed samples from 17:51:41 to 18:02:47 UTC could not be
recovered. The guard continued running, worker receipts survived, and a separate
VM-local observer retained later samples.

The coordinator allowed the next scratch build after checking independent
terminal state and all 13 preparation/worker units. Every unit exited cleanly,
and each lifetime total memory peak was below 6 GiB. That independently bounds
anonymous memory for the missing interval. This supports proceeding without
repeating replay, while preserving the disclosed sampling gap.

## Consequences

Do not claim continuous retained detailed sampling for this run. Keep the
original partial log, incident, supplement, terminal memory record, and
coordinator review. Successful replay permits scratch build; it does not
establish candidate audit, production initialization, or publication.

Keep generated human doctor dumps local under the same ignore policy as their
JSON equivalents. Production acceptance produced a 151 MB human rendering
beside its 177 MB JSON report. Preserve both original files locally and in the
private operation capture; retain their byte counts and hashes in the journal
receipts. This avoids accidentally adding regenerable diagnostic dumps to
version control. It does not discard or rewrite captured evidence.

## Links

- [Coordinator review](../evidence/releases/h16-event-preservation-2026-09-16/replay-coordinator-review.json).
- [Incident](../evidence/releases/h16-event-preservation-2026-09-16/log-path-replacement-incident.json).
- [Terminal memory verification](../evidence/releases/h16-event-preservation-2026-09-16/replay-terminal-resource-verification.json).
