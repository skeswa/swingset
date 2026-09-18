# D-0041: Consolidate the integrated agent workspace drafts

Recorded: 2026-09-17  
Decided by: agent  
the consolidation method is an agent implementation choice.  
Topic: Repository maintenance  
Supersedes: —  
Superseded by: —

## Decision

Replace the unnamed agent merge graph with one named local integration draft
above the existing `main`, followed by an empty working revision. Preserve the
integrated file tree and leave `main` and the remote unchanged. Forget the 15
inactive workspace registrations while retaining their directories.

## Why

The repository had 36 unnamed mutable revisions, divergent changes, and a stale
conflicted head. The integrated work already had validation evidence; rebuilding
it as separate feature revisions would require validating new intermediate trees.
One integration draft preserves the tested result and makes ongoing work clear.
This cleanup does not complete the remaining history and recovery plan.

## Recovery

Before changing the graph, retain the operation ID, a full tracked-file archive,
file hashes, and copies of side-workspace files that differ from the integrated
tree. Local recovery instructions are in
`~/.cache/swingset-jj-cleanup-20260917T141928Z/README.md`. The old graph remains
accessible through jj's operation history; no garbage collection is part of this
cleanup. These local backups are not a shared or permanent archive.
