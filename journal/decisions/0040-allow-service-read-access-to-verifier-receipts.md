# D-0040: Allow service read access to verifier receipts

Status: Proposed  
Recorded: 2026-09-16  
Accepted: —  
Acceptance source: Coordinator operational repair within the owner's authorized H16 release; no separate owner acceptance recorded.  
Topic: Release operations  
Supersedes: —  
Superseded by: —

## Decision

Give the service group read access to exactly two root-created H16 verification
receipts: `initialization-anchor-003.json` and
`initialization-verification-002.json`. Preserve root ownership and change their
group and mode to `root:swingset` and 0640 through checked open descriptors.
Preserve bytes, hashes, inode, and mtime. Exclude another writer with the existing
state lock and verify service readability before retrying the same build gate
with a fresh unit and output.

## Why

The verifier runs as root and writes private mode-0600 receipts. The production
build runs as the service user and hashes every referenced receipt. Its first
launch stopped on `PermissionError` before entering the build or creating its
result. Successful proof must remain readable to the service that consumes it.
Group read access is sufficient; service write ownership is unnecessary.

## Consequences

No application code, gate contents, state data, or proof checks change. Retain
the failed launch and the metadata repair receipt. This grants no public access
and does not recursively change other files or permissions.

## Links

- [Production release outcome](../investigations/2026/h16-production-release-2026-09-16.md).
