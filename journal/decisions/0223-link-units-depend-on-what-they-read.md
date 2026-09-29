# D-0223: Link units depend on what they read

Recorded: 2026-09-29  
Decided by: agent, 2026-09-29, under D-0175; source: phase 3 of the [architecture plan](../../docs/plans/architecture.md) (finding F20)  
Topic: Derivation dependencies  
Supersedes: the link units' dependency on the whole dancer cohort (`selected_project_dancer`, `revision:dancers`, the `canonical_*` groups); [D-0075](0075-reuse-dancer-readiness-only-within-owned-read-snapshots.md), [D-0081](0081-check-shared-link-prerequisites-before-event-expansion.md) and [D-0085](0085-check-shared-link-readiness-before-candidate-scans.md) (the cohort readiness checks it needed)  
Superseded by: —

## Decision

A `link/event/E` unit depends on the read keys its last run recorded, stored
in `derivation_reads`: E's event generation and replay version, each dancer
it used (candidates, registry confirmations, printed and linked ids), content
digests (E's events row, E's registry placements, E's missing-identity
findings, the journal, each source reference it bound) and `pool:E`, a
per-event pool version. Completion and cutoff replace the keys. A write
whose read values moved since the load raises `SupersededWorkError`.

- **Pool probe.** A dancer unit compares each dancer's name, surname word,
  initial, merge and judge eligibility before and after its write. It bumps
  `pool:E` for each event where the change could add or drop a candidate or
  change name rarity. When it cannot decide, or raises, it bumps more.
- **Reverse index.** A project pointer that moves queues the units whose
  read keys name it.
- **Readiness.** A link waits for its event, its resolution and the dancers
  its read keys name that have a unit (a scope, a queued unit or an
  observation), never a year: it reads its registry association as content,
  and a year that moves it queues the link. A WSDC id a sheet prints that the
  registry lacks is read as the row's absence and is no prerequisite, so the
  link stays ready and the dancer's first pointer queues it (amended
  2026-09-30, phase 3 review: F27 left 13 links never ready, and a year held
  behind the paced inventory held every link of its events).
- **Load.** The evidence and the read values observed at load come from one
  read transaction, so a write during the load is never certified as what
  the link read (amended 2026-09-30).
- **Checks.** A SQLite authorizer records the tables each project and link
  unit body reads; an undeclared read is logged and counted, and fails only
  in tests. Maintain re-derives 1% of links and years each week and alerts
  `[read-set-miss]` on a label that differs from the pointer's, or on a
  re-run that raises `NondeterministicOutputError` (amended 2026-09-30: F29).

## Why

One registry page re-queued all 2,934 links, and listing ready links read
the whole registry (26 to 29 minutes). On a clone of the 09-29 copy, readiness
of all links took 2.7 s, at about 1.7 keys per subject (about 325,000 rows,
75 MB). A read set of "rows returned" would miss a new or renamed dancer who
becomes a candidate, so pool effects get their own version.

## Alternatives and consequences

Recording every table read as a dependency is the cohort again. Any journal
or decision change still re-runs every link. The authorizer does not report
a table joined by `USING` or `NATURAL` whose other columns go unread; tests
close that gap, production does not (F25).

## Links

- [State reference, link read sets](../../docs/reference/state.md#link-read-sets-plan-f20)
