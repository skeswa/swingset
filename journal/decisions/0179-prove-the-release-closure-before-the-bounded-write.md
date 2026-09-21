# D-0179: Prove the release closure before the bounded write

Recorded: 2026-09-21  
Decided by: owner, 2026-09-21; source: "Fix and deploy now" after the diagnosis in the same session  
Topic: Build completion  
Supersedes: —  
Superseded by: —

## Decision

Build completion proves the pinned release closure once in an unbounded read
snapshot, then opens the 45-second write and rechecks that proof's exact
evidence at its three boundaries. The proof fingerprint gets its own byte cap
of 256 MiB, separate from the 32 MiB validation cap, because it hashes every
parent source generation row in full. When a proof cannot be kept, the worker
logs `closure_proof_unavailable`. The write bound itself is unchanged.

Candidate 007 is the deployed candidate 006 source plus these three modules,
not `main`, because `main` carries schema 30 to 32 and the database migrates
on open; those migrations stay undeployed until reviewed on their own.

## Why

After the hold was lifted on 2026-09-20 the first cycle published, then every
cycle failed at build completion with `WriteDeadlineExceeded`, and the
failure aborted the cycle before any parse work ran. On the live 5 GB
database one full closure validation takes about 17 seconds and completion ran
it three times inside the bound. The proof cache that should make the second
and third passes cheap never engaged: the event witness for 63 entries and 378
parents needs about 33.5 MB of row reads, just over the 32 MiB cap, so the
fingerprint silently returned None. With the fix, measured read-only against
the same database, the proof pass takes 17.7 seconds outside the bound and the
three rechecks take 4.8, 5.9 and 5.6 seconds inside it.

## Alternatives and consequences

Raising the write bound was rejected: it exists so operator controls can take
the writer within their 60-second wait. Hashing fewer columns in the proof was
rejected: the proof must notice everything full validation would. The cost of
this choice is one extra full validation per build, outside the bound, and
proofs that read up to 256 MiB of rows; both grow with the closure and are
visible in cycle timing.

## Links

- [Receipt](../evidence/runtime/candidate-007-2026-09-21/receipt.json)
- [Candidate history](../../docs/reference/candidate-history.md)
