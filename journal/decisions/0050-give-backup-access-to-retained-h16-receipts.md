# D-0050: Give backup access to retained H16 receipts

Recorded: 2026-09-17  
Decided by: agent  
Topic: Backup operations  
Supersedes: —  
Superseded by: —

## Decision

Give the `swingset` group read access to 33 named H16 operation receipts and
read/search access to the retained H16 performance directory. Keep root
ownership and preserve file contents, inodes and modification times. Add no
world access. Perform the narrow repair under the existing writer and control
locks while ordinary units remain held.

## Why

The fresh post-publication checkpoint failed because the service user could
not read `deployment-intent.json`. Inspection found the same problem on the
listed supervision receipts, plus one unsearchable operation directory.
The existing checkpoint implementation retains these operation records.
Skipping them would weaken recovery evidence; running every backup as root
would leave the ordinary service defect unresolved.

This follows the earlier receipt-access repair in D-0040. It is an authorized
operational implementation choice, not newly recorded owner acceptance.
The failed checkpoint receipt remains preserved; the retry uses a new operation
directory. No live database contents, source artifact bytes, or holds change.

## Links

- [Exact repair helper](../tools/runtime/repair_h16_backup_read_access.py)
- [Prior receipt-access repair](0040-allow-service-read-access-to-verifier-receipts.md)
- [Continuation evidence](../investigations/2026/v2-continuation-2026-09-17.md)
