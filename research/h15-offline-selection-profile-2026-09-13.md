# H15 retained offline selection profile

The first 70 disposable replay scopes took about 304 seconds. Profiling shows
that selecting the next scope dominated the work. One actual dancer projection
completed in 0.043 seconds, including its durable attempt and generation receipt.

The retained schema14 scratch was `/var/tmp/swingset-h15-offline-replay`.
The control runtime was
`/nix/store/694311m4fcif32w774bs8vpkc4iv6d9a-source`.
No source requests, production writes, or publication occurred. The actual
projection added one scope generation to this disposable scratch only.

A read-only comparison on the same resulting state selected dancer `10029`
with both implementations:

| Measurement                     | Pinned runtime | Narrow optimization |
| ------------------------------- | -------------: | ------------------: |
| Profiled next-offline selection |        7.898 s |             0.525 s |
| Python function calls           |  101.2 million |        1.56 million |
| Prerequisite calculation        |        6.221 s |             0.020 s |

These are one paired cProfile measurement, including profiler overhead, not
an end-to-end throughput estimate. The measured selection improvement is
15.1 times. The prior standalone selection profile took 8.184 seconds.

The old selector opened separate catalog caches for nine stage/kind groups.
Each event prerequisite calculation then iterated the full project catalog.
The optimization shares one unchanged read cache across the fair selection
and caches catalog subsets for prerequisites. It preserves dependency ordering,
fixed map/inventory dependencies, mapped source-event dependencies, fairness,
and cache invalidation on writes. Mutable transaction caching remains disabled.

Thirty-five existing fairness, derivation, and H15 scenarios pass. Two focused
regressions verify a single catalog read across 200 blocked events, unchanged
prerequisite ordering, and invalidation after a new dancer scope appears.

The full profile receipt is
[`verification/h15-offline-selection-profile-20260913.json`](verification/h15-offline-selection-profile-20260913.json).
The source changes are confined to `schedule/fairness.py` and
`state/derivation_dependencies.py`. Production was not repinned by this audit.

## Actual 100-scope replay after H16 freeze

A copy of the frozen working runtime ran 100 project scopes on the same
isolated scratch: two calendar scopes, 49 dancers, and 49 source indexes.
All succeeded. This run did no parsing, requests, building, or publication.
Accepting the copied runtime bundle changed only `recipe/runtime` and
`version/repository` in the disposable state and took 2.581 seconds.

| Measurement                              |   Actual result |
| ---------------------------------------- | --------------: |
| Replay wall time                         |  54.566 seconds |
| Work selection                           |  43.926 seconds |
| Derivation, including durable completion |  10.458 seconds |
| Immutable generations                    |        71 → 171 |
| Immutable output payload growth          |   291,240 bytes |
| Shared dependency manifest growth        |    58,984 bytes |
| Closed SQLite file growth                | 1,323,008 bytes |

The copied runtime contains 293 files at
`/var/tmp/swingset-h16-100scope-source`; its source manifest digest is
`55bd3fd935ed0f8367af07cd3288592d3a3926cb95236c269a935e14fc29c64e`.
Its `SOURCE.json` records every copied file hash. The measured database is
still `/var/tmp/swingset-h15-offline-replay`, and all connections are closed.

At this observed mixture of base scopes, 35,000 scopes would take about
5.3 hours. This is an arithmetic extrapolation, not a full graph forecast:
large inventory, map, history, event and link scopes were not measured in
this 100-scope run. Selection still accounts for most elapsed time. No
additional cache architecture or production replay was introduced here.

The [machine receipt](verification/h16-100scope-replay-20260913.json)
retains each actual unit, timing, accepted bundle digest, and before/after
storage totals. The [exact executed script](verification/h16-100scope-replay-script-20260913.py)
is retained as run evidence; its paths refer only to that disposable scratch.

## Read-only link dependency profile on the full replay scratch

After the first two bounded full-replay invocations, the exact H16 pin
`/nix/store/d8bwqgbqi94b19l0wfgqr7a2gir1xkf5-source` profiled readiness and
desired inputs for `2026-06-jack-jill-o-rama` (1,829 retained entries).
The scratch was `/var/tmp/swingset-h16-full-replay`. No linker executed.

| Operation           | Profiled wall time | Main cost                                                                   |
| ------------------- | -----------------: | --------------------------------------------------------------------------- |
| Link readiness      |      2.829 seconds | 1.765 seconds checking 29,049 prerequisites; 1.045 seconds discovering them |
| Desired link inputs |      1.302 seconds | 1.068 seconds scanning the full project catalog three times                 |

Readiness returned false because structural derivations are still pending.
The measurement therefore does not claim to characterize a fully settled
link cohort. It does expose avoidable catalog work and 203,350 SQLite calls
in readiness. The full registry dependency is intentional: its shared set
contains 29,048 selected dancer generations and is 4,142,759 bytes. The top
manifest contains 15 dependencies in 1,853 bytes, including that set's hash.

The exact desired fingerprint is
`db83e5a6575b49a43d1263eea18a2e47e7c81e5386045fb6ad34841c2362ca97`.
The [profile receipt](verification/h16-link-selection-profile-20260913.json)
records all dependency set sizes and cumulative call costs. The
[executed script](verification/h16-link-selection-profile-script-20260913.py)
is retained. The reader closed normally. This audit made no runtime edits.

The bounded follow-up replaces link dancer/history catalog scans with exact
kind queries and checks the requested event through its six retained catalog
origins. It preserves registered and queued custom scopes, physical observation
and ownership scopes, shared history inference, and the original dependency
ordering. It does not change readiness ordering or mutable transaction caching.

| Same-state operation | Pinned catalog | Exact subset query |
| -------------------- | -------------: | -----------------: |
| Desired link inputs  |  1.302 seconds |      0.263 seconds |
| Link readiness       |  2.829 seconds |      1.830 seconds |

The desired fingerprint, generation ID, top manifest size, and every dependency
set hash, member count and byte count were identical. All 29,048 registry
members remain selected. These are profiled read operations on the paused
scratch, not an actual link worker or production throughput estimate.
Readiness still spends 1.749 seconds checking all registry prerequisites.

Seventy-five focused link/map selection cases pass, including observation-only
scope evidence, non-project queues, standalone source-index ownership versus
mapping, actual event/source-event/registry-placement facts, 1,000 dancer scopes,
and rollback invalidation inside a query. Scoped mypy and Ruff pass.
The [comparison receipt](verification/h16-link-selection-optimized-20260913.json)
and [executed overlay script](verification/h16-link-selection-optimized-script-20260913.py)
retain the exact measured module hash and profiles. No checkpoint, accepted
bundle, scratch derivation, or production state changed during this comparison.

The final bounded readiness change bulk-loads dancer pointers, immutable proofs,
raw input versions, watch membership/version pairs, and the shared source version
inside one SQLite snapshot. Both the ordinary and bulk paths call the same base
certificate and proof encoders. Every missing or mismatched proof falls back to
the existing full currentness check. Active consistency groups retain their
original path. No answer is cached across calls, writes, or rollbacks.

On the same retained scope, readiness fell further to 0.633 seconds; desired
inputs took 0.258 seconds. The exact fingerprint and all dependency set hashes,
counts and sizes remain identical. The full registry cohort is still checked.
The [bulk comparison receipt](verification/h16-link-selection-bulk-20260913.json)
and [executed script](verification/h16-link-selection-bulk-script-20260913.py)
record all four overlaid module hashes and call profiles.

Differential tests compare the bulk answer with individual `current()` calls
using the retained registry lookup body. They cover changed source payloads,
removed observations, snapshot transport, source selection, watch sources,
accepted recipes, harmless token advancement, missing/forged proofs, rollback,
physical scopes whose registration was deleted, and active consistency groups.
A two-connection test verifies that all bulk reads see one snapshot even when
new source input commits between queries. A healthy 51-scope fixture performs
at most six SQL reads without entering the slow fallback.
