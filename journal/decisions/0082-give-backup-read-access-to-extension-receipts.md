# D-0082: Give backup read access to extension receipts

Recorded: 2026-09-17  
Decided by: agent  
Topic: Recovery operations  
Supersedes: —  
Superseded by: —

## Decision

Give the service group read access to the exact retained extension preflight
and live-migration receipts. Keep root ownership, contents, inodes and
modification times; add no world access. Bind both files by their retained
hashes and take the writer and control locks while ordinary units remain held.

## Why

The first schema-28 checkpoint stopped because these two root-owned receipts
had mode 0600. The operation inventory found no other unreadable paths.
Skipping retained receipts would weaken recovery evidence. This follows
[D-0050](0050-give-backup-access-to-retained-h16-receipts.md) for the new rollout
files, without extending permissions to other state or secrets.

The [repair receipt](../evidence/runtime/schema28-checkpoint-2026-09-17/receipt-access-001/receipt.json)
records the two exact paths and preserved hashes. The failed checkpoint stays
retained; retry uses a fresh destination and the same reviewed schema-28 helper.
This is an operational implementation choice under existing authority, not new
owner stage acceptance, deployment or publication.
