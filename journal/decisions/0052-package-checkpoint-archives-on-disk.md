# D-0052: Package checkpoint archives on disk

Status: Proposed  
Recorded: 2026-09-17  
Accepted: —  
Acceptance source: —  
Topic: Backup resource use  
Supersedes: —  
Superseded by: —

## Decision

Set the backup service's temporary directory to `/var/tmp`, which is disk-backed
on the recorded OrbStack worker. Keep its private temporary namespace. Use the
same setting for the supervised checkpoint upload.

## Why

The fresh H16 checkpoint contains 8,739,511,386 bytes. Its first upload attempt
created a 3.8 GiB partial transport archive under RAM-backed `/tmp`, exhausting
the operation's 4 GiB memory limit. Changing the temporary filesystem avoids
spending RAM on an archive without raising the memory limit. The completed
checkpoint remains intact and the retry verifies it before upload.

The failure and partial archive remain evidence; a new operation directory
records the retry. This setting changes packaging, not artifact closure, source
request budgets, private archive acknowledgment or public publication.

## Links

- [Backup service](../../nix/module.nix)
- [Continuation evidence](../investigations/2026/v2-continuation-2026-09-17.md)
