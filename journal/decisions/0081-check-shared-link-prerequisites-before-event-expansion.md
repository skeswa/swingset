# D-0081: Check shared link prerequisites before event expansion

Status: Proposed  
Recorded: 2026-09-17  
Accepted: —  
Acceptance source: —  
Topic: Offline selection cost  
Supersedes: —  
Superseded by: —

## Decision

Check the database-defined common dancer cohort before assembling each link
event's complete prerequisite tuple. Reuse that boolean only within the
existing owned read snapshot, using a key for the exact shared database query.
Expose one shared cohort query used by both prerequisite construction and
readiness. Keep per-event and history prerequisites, consistency-group behavior,
currentness and admission checks unchanged.

## Why

The [second comparison](../evidence/runtime/offline-selector-profile-2026-09-17/comparison-002/report.json)
removed repeated full proof scans but still stopped at 30 seconds. Among 285
link candidates it recorded over 25 million work-unit hash calls and repeated
cohort construction. The entire shared cohort is identical within the owned
snapshot; requiring each event to reconstruct and hash it defeats much of the
reuse. This profile overlapped the held backup, so wall-time comparisons are
not isolated throughput measurements.

The shared query includes registered, queued and physical dancer scopes. Its
membership and proof are bound by the same database snapshot. Caller-owned
transactions recompute, and mutation, rollback and a new snapshot cannot inherit
old answers. Arbitrary caller-provided cohort checks retain their exact keys.
Selection ordering and the complete fallback population do not change.

This is a local implementation choice requiring focused tests, independent
review and another source-bound profile. It is not production acceptance or
measured service calibration.
