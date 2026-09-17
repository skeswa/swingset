# D-0106: Archive large files and scrub local history

Status: Accepted  
Recorded: 2026-09-17  
Accepted: 2026-09-17, Sandile Keswa  
Acceptance source: Owner requested a history scrub, selected over 1 MiB, and required a verified external archive before removal  
Topic: Repository size and evidence preservation  
Supersedes: D-0105's allowance for tracking explained size exceptions  
Superseded by: —

## Decision

Archive and remove contents over 1 MiB from local history using jj. Preserve
smaller files and published commits. Keep required large files as ignored local
copies, backed by a verified external archive. The
[evidence guide](../evidence/README.md#restore-archived-large-files) owns retrieval
instructions. The size check now rejects any tracked file over the cutoff.

## Why and limits

Eight distinct large contents occupied thirteen paths across thirteen unpublished
revisions. Rehearsal compared all 111 revision trees; only those large contents
were removed. The full repository backup preserves old revisions and operation
history. After verifying the local rewrite, obsolete operations and objects can
be pruned. Main and its origin reference do not require replacement or a push.

This preserves original evidence bytes but makes external retrieval necessary
for clean-checkout tests and some operating checks. The archive is verified on
the owner's machine, not yet replicated off-site. No production deployment or
dataset publication is part of this change.

The [compact receipt](../evidence/runtime/history-scrub-2026-09-17/receipt.json)
records exact hashes, paths and validation results. D-0105's compact-output
practice remains in effect; the old file-size exception is replaced here.
