# D-0033: Repair production control-lock ownership

Status: Proposed  
Recorded: 2026-09-16  
Accepted: —  
Acceptance source: Coordinator operational repair within the owner's authorized H16 release; no separate owner acceptance recorded.  
Topic: Release operations  
Supersedes: —  
Superseded by: —

## Decision

Restore service ownership and mode 0600 on the existing production
`control.lock`. Check the exact regular file and acquire the writer lock,
then the control lock, before changing its owner and mode through its open
descriptor. Preserve its inode and contents. Retry preparation with the same
reviewed gate, initializer, and durable marker, using a fresh output and unit.

## Why

The second preparation attempt saved its intent marker but failed opening
`control.lock`, before migration or input acceptance. The lock is an empty
root-owned mode-0644 file dated 2026-09-13; the database and writer lock are
service-owned mode 0600. The runtime opens the control lock for reading and
writing, which fails for the service user. Its original creator is unverified.

## Consequences

This restores normal service access without changing application code or
control decisions. Replacing the file could split lock coordination, so its
inode must remain unchanged. Preserve the failed receipts and verify database
authority before retrying. The existing marker makes interrupted preparation
resumable through normal SQLite checks.

## Links

- [Production release outcome](../investigations/2026/h16-production-release-2026-09-16.md).
- [Legacy preparation compatibility](0032-handle-missing-legacy-runtime-recipe-during-initialization.md).
