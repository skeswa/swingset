# D-0214: A derivation backlog does not pause acquisition

Recorded: 2026-09-27  
Decided by: agent, 2026-09-27, under D-0175; source: phase 1 review finding on the watchdog's blind spot ([architecture plan](../../docs/plans/architecture.md))  
Topic: Acquisition  
Supersedes: —  
Superseded by: in part, [D-0216](0216-the-work-queue-leases-backs-off-and-quarantines.md) (only runnable parse units count)

## Decision

Once derivations exist (schema 14 on), the `pending_work_items` high-water
mark (10,000) counts only parse rows. Project and link rows are hints and no
longer pause acquisition. A code-scope or `recipe/env` accept queues a hint
for every unit it makes stale, and a replay epoch queues its readers'
units, so the watchdog, which reads only `pending_work`, sees that work (I4).
The parse marks (1,000 items, 128 MiB) are unchanged.

## Why

Before phase 1 a code deploy re-queued every parse and left derivations
stale by fingerprint with no rows, so their re-run never closed fetching.
With the hints a project-scope deploy queues about 32,900 project units, and
events and source events wait up to 12 hours behind the paced inventory
(D-0210): counted, they would hold fetching that long on every deploy.
Derive re-checks fingerprints either way, so the rows are not the backlog's
measure (I5: loops depend on facts, not each other's health).

## Alternatives and consequences

The reviewer's weaker option, per-stage not-current counts in the cycle
record, needs a fingerprint check of all 35,800 units every cycle. A large
derivation backlog now coexists with fetching; the parse marks still bound
the work fetching creates.

## Links

- [Scheduling](../../docs/reference/scheduling.md)
