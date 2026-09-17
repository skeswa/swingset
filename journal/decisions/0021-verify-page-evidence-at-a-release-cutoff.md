# D-0021: Verify page evidence at a release cutoff

Status: Proposed  
Recorded: 2026-09-16  
Accepted: —  
Acceptance source: The owner authorized continued plan implementation; release integration and operating acceptance remain pending.  
Topic: Release coverage  
Supersedes: —  
Superseded by: —

## Decision

Implement an initially unexposed, bounded verifier for one distinct source
request. Given a release cutoff and retained archive, return acquired and
interpreted outcomes as true, false, or unknown, with exact supporting receipts.
Use the caller's read snapshot. Start no recovery, source request, admission,
projection, or progress update.

Snapshot fetch, generation creation, and accepted-decision times must all
qualify under the cutoff. Apply current admission policy and related revocation
checks. An older usable interpretation may survive a later blocked attempt.
Verify generation content and the actual retained artifact bytes, rather than
trusting scheduling observations or merely present paths.

## Why

The first release witness can count selected admitted support but cannot state
full local acquisition or interpretation totals. A separate verifier makes its
evidence and resource limits reviewable before adding those claims to releases.

Bound returned rows, candidates, JSON, compressed and decoded artifact bytes,
and cooperative elapsed time. Exhaustion is unknown, not absence or success.
Keep the verification timestamp distinct from the source-evidence cutoff.

## Integration requirement

Only a fully assessed pinned enumeration can produce a total; otherwise retain
null and disclose unknown members. Later arrivals must not invalidate an old
release merely by changing the set of current candidates. File deletion or
corruption can occur without SQLite changes, so positive artifact evidence
cannot rely on the existing database-only proof cache. Define and test that
validation boundary before exposing the helper's counts publicly.

## Links

- [Pinned release evidence](0020-pin-event-enumerations-in-release-evidence.md).
- [Coverage contract](../../docs/reference/data-model.md#event-completion-coverage).
- [History and recovery plan](../../docs/plans/history-and-recovery.md).
