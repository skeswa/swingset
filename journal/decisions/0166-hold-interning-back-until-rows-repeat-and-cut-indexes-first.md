# D-0166: Hold interning back until rows repeat, and cut indexes and JSON first

Status: Accepted  
Recorded: 2026-09-18  
Accepted: 2026-09-18, Sandile Keswa  
Acceptance source: Session instruction on 2026-09-18: "I approve D-0166"  
Topic: State retention and recovery cost  
Supersedes: —  
Superseded by: —

## Decision

This is the record the plan's step 1 asks for: which later steps run, in what
order, from the numbers.

1. **Do not deploy schema 30 (interning) yet.** On a scratch copy of held
   checkpoint 004 the migration makes the file 6.3% larger after reclaim,
   because rows do not repeat: every scope has one generation. Re-measure with
   `journal/tools/runtime/measure_state_storage.py` on a copy taken after the
   pipeline has recomputed for several weeks. Interning pays only when the
   savings, (1 − distinct/total) × payload bytes, exceed the reference
   overhead, 430 MB on this copy for 502 MB of payload, so more than about 85%
   of rows must be exact repeats. If they are not, redesign the references to
   name payloads by integer row id instead of a 64-character digest before
   deploying, or drop the step.
2. **Reorder the migrations before any deployment.** Migrations 31 (declared
   finding references) and 32 (apply notes) serve step 3 and do not depend on
   interning, but they sit after 30 and cannot be applied without it. Move
   interning to the end of the sequence so step 3 can deploy alone.
3. **Add a step for the derivation row indexes and the source generation
   JSON.** The two unique indexes on `derivation_rows` cost 492 MB, more than
   half the table again, and `source_generations` holds 720 MB of JSON.
   Together with `canonical_scope_rows` (532 MB) they are 34% of the file.
   Measure which columns and indexes carry those bytes before designing.
4. **Step 3 stays the main lever.** Growth is the count of generations, each
   costing about 20 KB of labels and dependency-set manifests plus its rows.
   The local and durable lists, holds, plan and apply are what bound that
   count once step 4 gives superseded generations somewhere to go.
5. **Both retention defaults stay provisional.** The cap of 8,000,000,000
   bytes is 1.6 times the measured file; a schema-30 migration peaks 1.9 GB
   above the file and a reclaim needs twice the file free, which the 256 GiB
   machine bound covers ([D-0133](0133-bound-the-worker-disk-and-remove-rehearsals-eagerly.md)).

## Why

The measurement ran on 2026-09-18 against a scratch restore of held
checkpoint 004 ([investigation](../investigations/2026/state-storage-measurement-2026-09-18.md),
[evidence](../evidence/runtime/state-storage-measurement-2026-09-18/)):

| Measure                            | Value                                                |
| ---------------------------------- | ---------------------------------------------------- |
| File size, schema 29               | 5,016,936,448 bytes, free list 0                     |
| Generations, rows                  | 34,987 and 1,669,989 rows, 502,109,081 payload bytes |
| Distinct payload digests           | at most 1,669,607, 0.9998 of rows                    |
| `derivation_rows` with indexes     | 1,366,700,032 bytes, 27.2%                           |
| `source_generations`               | 731,418,624 bytes, 14.6%                             |
| `canonical_scope_rows`             | 532,299,776 bytes, 10.6%                             |
| `derivation_dependency_sets`       | 438,067,200 bytes, 8.7%                              |
| After migration to 32 and reclaim  | 5,333,405,696 bytes, 6.3% larger                     |
| Migration, reclaim                 | 92 s and 34 s; file peaks at 6,890,405,888 bytes     |
| Checkpoint create, verify, restore | 268 s, 43 s, 227 s for an 8.77 GB tree               |

The plan's step 2 rested on rows repeating between recomputations. Production
has been held since the derivation tables arrived, so no scope has been
recomputed and nothing repeats yet. The plan's own rule for this case is to
say what the biggest cost is and add a step for it rather than guess.

## Alternatives

- Deploy schema 30 now anyway. Rejected: a 92-second migration that grows the
  file by 316 MB and buys nothing until history exists.
- Drop step 2 from the plan. Rejected for now: recomputation will start when
  the hold lifts, and the ratio must be measured then. The code stays tested.
- Deploy step 3 as it stands, accepting schema 30 with it. Rejected: it ties
  the useful change to the unproven one.

## Consequences

Nothing deploys from this plan until the migrations are reordered. The step 1 gate is met: receipts are retained, the
counts match the copy, every page is accounted for, and this record names the
order. The next measurement needs a copy with recomputation history, which
does not exist until collection resumes.

## Links

- [Investigation](../investigations/2026/state-storage-measurement-2026-09-18.md)
- [Plan](../../docs/plans/bounded-state-and-archive.md)
- [D-0119](0119-bound-state-by-interning-and-one-closure.md), [D-0127](0127-measure-state-storage-read-only-on-a-copy.md), [D-0158](0158-build-steps-2-and-3-before-the-measurement-and-gate-the-rest-on-it.md)
- [D-0167](0167-intern-derivation-payloads-in-the-last-migration.md) carries out point 2: after the reorder interning is schema 32, the last migration.
