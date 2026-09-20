# D-0176: Resume automatic collection and publication; give the hold an owner and an expiry

Recorded: 2026-09-20  
Decided by: owner, 2026-09-20; source: "Please enact all of these suggestions", following the 2026-09-16 instruction to remove the hold on deployment and publication once H16 completed  
Topic: Release operations  
Supersedes: [D-0126](0126-pause-v2-overnight-after-integrated-local-validation.md) on stopping at the local-validation boundary, and the "runtime interlock" reading of the hold in the 2026-09-17 handoffs  
Superseded by: —

## Decision

Remove `/var/lib/swingset/operator-hold` and let the existing timers collect,
build and publish on their own, as they did before 2026-09-13. The deployed
candidate 006 runtime is what runs; nothing new was deployed.

From now on a hold file names who set it, why, and a review-by date. `doctor`
reports the hold's age and marks it overdue after 48 hours, and an overdue
hold is the first line of [status](../../docs/status.md). A hold covers one
named review and is removed when that review ends.

## Why

The hold was set on 2026-09-13 at 04:17 UTC for the V2 deployment and G1
probe review. That review ended with the H16 release on 2026-09-16 and
production input acceptance on 2026-09-18, but the file stayed and every
15-minute cycle since was skipped. The owner authorized removal on 2026-09-16.
Candidate 006 is deployed, migrated and audited; no further gate was defined.

Before removal, a read-only doctor run exposed a service fault: two entries
under `/var/lib/swingset/uv-cache/archive-v0` were root-owned since
2026-09-17 22:44 UTC, and the kernel's protected hardlinks setting made the
service's package sync fail for the `swingset` user, so the first post-hold
cycle would not have started. Ownership was corrected to `swingset`, the exact
`ExecStartPre` sync was rerun and passed, and the hold was removed at 23:09:58
UTC on 2026-09-20 with the cycle started directly. The
[receipt](../evidence/runtime/collection-resume-2026-09-20/receipt.json)
records the commands, the pre-resume doctor snapshot digest and the first
cycle outcome.

## Links

- [Holds](../../docs/guides/operation.md#holds), the hold file format and doctor report
- [Process review](../investigations/2026/process-review-2026-09-20.md)
