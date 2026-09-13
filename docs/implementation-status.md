# v1 implementation status

For current v2 work, deployment, and the successful live G1 receipt, see
[v2 progress](v2-progress.md). Stage V1 of the v2 plan is complete at public
commit `7cfcf4ec5dbc994d91f3e4d816f43b3abe16637b`; V3 is complete at public commit
`4653f3a3a6076d3af474c28f7bd0e93998ca0a9c`. V2 and V4 are complete at public commit
`81121dcc7d62a1db9e76ee1c49a6b90e1f4c5653`; V5 infrastructure and V6 are in progress. The v1 receipts
below retain their original observation dates.

Checked 2026-09-11 UTC. This records evidence against
[the implementation plan](../design/implementation-plan.md); it does not
replace its done criteria. The original implementation was split into 12
described Jujutsu revisions and pushed to `main` (through `2d291571`).
The active corrected release is `2089803d379ef4233ee45a49193ce97befcf0911`;
source acceptance is tracked in issues #10 and #12–#14; #15 records the repair.

## Implemented

| Package | Code and local verification                                                                                                                                                                                                                                                            |
| ------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| WP0     | Python package, frozen uv dependencies, Nix flake, strict typing, CI, config, clock, structured logs. Native imports work on the Mac and arm64 NixOS.                                                                                                                                  |
| WP1     | SQLite migration, ids, enums, typed observations and canonical records, writer lock, durable work, atomic input acceptance.                                                                                                                                                            |
| WP2     | Shared host gate, robots cache, request/byte budgets, conditional requests, retries, classification, compressed raw archive and extracts. Mock-transport tests cover failure and pause behavior.                                                                                       |
| WP3     | Discovery, watch lifecycle, cycle budgets, calendar extraction, mapping, transactional projection, interruption recovery, pause/resume. A quiet calendar cycle does no fetch or stage work.                                                                                            |
| WP4     | Reusable NixOS module, CLI package, cycle/backup/summary timers, service hardening, graceful stop handling. The OrbStack NixOS writer is installed with publication and scheduled jobs enabled.                                                                                        |
| WP5     | Explicit Arrow schemas, invariant checks, suppressions, review queue, changelog, immutable candidates, dataset card, Hub adapter, publication reconciliation, complete checkpoints, locked restore and GC. Offline tests exercise publication failure boundaries and artifact closure. |
| WP6     | Registry parser and projection, verified missing-id classifier, durable sweep/probe cursors, daily trickle, archived dump cross-check and replay. Real found/miss fixtures and fake-clock cursor tests.                                                                                |
| WP7a    | Name/division normalization, nickname seed, canonical contest projection, event/role assignment, manual overrides, judge restrictions, identity candidates and invalidation.                                                                                                           |
| WP7b    | EEPro index, autoindex and round adapters; child invalidation and slow refresh clocks. Real Summer Hummer fixtures, named judges, paired finals bibs, date ranges, and Count ranking verified.                                                                                         |
| WP8     | scoring.dance sitemap/index/event/round adapters and real fixtures; source-id and registry confirmations, refresh watches, expected-points checks.                                                                                                                                     |
| WP9     | WDR rounds/awards adapters, validator polling, expected-403 retirement, seed script and 13 usable source overrides. Complete real rounds/awards captures for all 13.                                                                                                                   |
| WP10    | Operator commands, diagnosis, summaries, reparse, runbook, collection/removal README, issue templates and generated enum documentation.                                                                                                                                                |

Some file boundaries differ from the plan's suggested layout. Event name
normalization lives in `normalize/events.py`; source-id, bib reuse and registry
confirmation live in the linking service. They use the same transactional
work and observation boundaries.

## Checks executed

Release verification at active revision
`2089803d379ef4233ee45a49193ce97befcf0911`: 209 tests passed; Ruff and strict
mypy passed. These checks ran through the Nix development shell. The full build
completed in 34.694 seconds with about 3 GB peak memory. An earlier 8 GB
out-of-memory incident preceded the repair and is excluded from unattended
operation evidence.

Run the complete local checks with:

```sh
nix develop --command uv run ruff check .
nix develop --command uv run mypy
nix develop --command uv run pytest -q
```

The suite covers atomic input acceptance and restart, observation ownership,
projection replacement, linking invalidation, build/publication recovery,
checkpoint restore, host gates and scheduler policies. A subprocess crash
harness kills calendar cycles before and after transaction boundaries and
restarts them; it also sends SIGTERM during a request and verifies a clean
next cycle. Regression checks cover sparse sitemap evidence preserving richer
event metadata, repeated override mapping, and version-only repair of stale
materialized rows. This is offline recovery evidence, not a production soak test.

Live requests used `swingset fetch-one` or `cycle` through the configured gate:

| Source        | Observed result                                                                                                                                                                                                                                                                                                                                                  |
| ------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| WSDC calendar | Full archived body produces 172 observations and 169 deduplicated events. A second cycle was quiet.                                                                                                                                                                                                                                                              |
| WSDC registry | Dancer 1 produced a found record and three placements. Ids 1,000,000 and 1,000,001 returned the same verified 404 error body. The comparison dump was archived with SHA-256 `ae7f2b9d688b69b49d08dfc718f60e5ef6b4b6561054d2503e8c94b8ae5e1d53`. The partial production mirror is at cursor 3,297 against the 27,039-ID dump; the full sweep remains in progress. |
| scoring.dance | Archived sitemap, recent index, event 418 and all 12 rounds. Full local build: 6 contests, 12 rounds, 317 entries, 336 callbacks, 62 placements, 1,684 callback marks and 420 final marks; 236 entries have confirmed source IDs. Bib identity is scoped by contest and role.                                                                                    |
| WDR           | Complete rounds and awards captures for all 13 known source URLs. The acceptance build contained 285 contests, 451 rounds, 11,727 entries, 3,144 placements and 3,759 callbacks attributed to WDR. Repeated rounds fetch returned `NotModified`/304.                                                                                                             |

Archived parser fixtures live under `src/swingset/sources/*/fixtures/`.
Synthetic fixtures are labeled under `tests/fixtures/sources/`. Raw source
semantics that are not established by these samples remain unverified.

The `swingset` OrbStack machine runs NixOS 25.11 in UTC as the selected writer.
Native Python imports passed and the cycle, backup, and summary schedules are
installed and have each run successfully. Publication is enabled. The separate
`swingset-restore` VM recovered both legacy flat and packed checkpoints, ran a
clean dry cycle after each recovery, and was then stopped without collection
timers. The dependency installer needed a 120-second download timeout on the
cold VM; source fetches retain their separate 30-second timeout.

## Acceptance still pending

- The controlled calendar publication, unchanged repeat, private backup, and
  fresh-machine recovery passed; issues #7 and #8 are closed. Both legacy flat
  and packed archive formats restored against the public head. Scheduled
  activation passed and issue #9 is closed; extended observation remains in #13.
- The owner reported EEPro permission received on 2026-09-08; see its source
  playbook. Real Summer Hummer fixtures, `Count` semantics, a complete local
  event build, and initial results publication passed. The live-weekend
  measurement remains pending under issue #12.
- Complete the seeded registry bootstrap sweep through normal cycles
  and compare the completed mirror with the archived 27,039-ID dump. The
  partial mirror is at cursor 3,297; no completion or full-coverage claim
  exists yet.
- Observe automatic finalist confirmation after matching registry evidence is
  published and collect a month of scoring.dance snapshots for nonce and
  Cloudflare stability. The owner's roughly one-week posting estimate is an
  expectation, not an acceptance deadline.
- Obtain the exact Jax Westie Fest 2026 scores UUID. Swingapalooza is verified;
  all 13 known event URLs have complete rounds and awards captures. Measure one
  live weekend's conditional-response rate.
- Resolve WDR `S<n>` and finals bib ownership from stronger evidence. The NASDE
  attribute-group legend is verified; other groups remain unverified. The
  adapters preserve unknown values and withhold unsupported canonical claims.
  Fifteen rounds with unlabeled Am/Pro roles and one masked bib remain ambiguous
  in the captured WDR evidence; their raw observations are retained.

The live systemd stop-during-request check passed on 2026-09-09. Run
`run_20260909T141539Z` finished its single calendar request and stopped in
1.35 seconds. The next dry cycle succeeded, SQLite integrity and foreign-key
checks passed, and no work was left pending. See closed issue #6.

Historical receipt: the initial reviewed results publication used candidate
`cand_984c84cc9dd34af8` at public commit
`a4abf85ff6e6e0ad4dd2088e130668987856613a`. Its 17 schemas, hashes, provenance,
unique keys, card counts, and a remote DuckDB query over 3,368 placement rows
passed. The build contains 312 contests, 255 events, 12,397 entries and 498
rounds. The Hub reports all 17 splits ready; events, placements, entries, rounds,
and final-marks previews each returned HTTP 200 with 100 rows. The selected writer uses `dryRun = false`. Private archive
commits `5ad249109d6dc866b9d9d4432ecac2fea8126708` (legacy flat) and
`c691a760015698247d157c2c2cf738fc712ea53e` (packed) passed fresh-machine
restore. The results-state private backup completed successfully at 15:37:05
UTC at private archive commit `c716bf7f7e8eed8369828c26e2a2434f3c08aaf0`. Initial
scheduled jobs and final activation passed; issue #9 is closed. See the [runbook](runbook.md) for the active handoff and restore
evidence.

The current reviewed public dataset is candidate `cand_d06d6d9e48fd41a2` at
public commit `ec6b7bbb84be9e5252d55c6bb1eae3feef05dd5b`. It contains 573
events, including 96 with results; 13,922 placements; 57,505 entries; 3,229
dancers; and 33,427 registry placements. There are 1,269 registry event
mappings and 9,471 entries with a WSDC ID. The registry mirror remains partial
at cursor 3,297 against the archived 27,039-ID dump.

Matching private backup commit `556e04b59b8404a3e5a12f91c4804eecbf8fee2f`
passed verification: 9,240 checkpoint files and the 691,200,000-byte transport
hash and size matched, as did the public manifest and `PUBLISHED` receipt.
Before resumption, doctor showed no pending work or candidates, host or operator
pauses, or restore marker. All three review guards were removed and timers were
resumed at 2026-09-11 03:29:39 UTC. The cycle service was running at handoff;
this is not evidence that its first resumed cycle completed. Backup and summary
were waiting for 04:00 and 08:00 UTC respectively.

The preceding public commit `c9789bad676e64424aa9ab7fc581b9aefe16868c`,
candidate `cand_b2c68aaa3b7b4f3c`, and checkpoint
`245f6a4f507581254c989ee20aa7529f0f506a5b` remain historical receipts. Their
cursor-2 and timer observations do not describe current state. Issues #10,
#12, #13 and #14 remain open; issue #15 records the completed correction release. See
[the 2026-09-10 data-quality repair report](../research/data-quality-repair-2026-09-10.md).

The initial rollout hit an EEPro request backoff and two registry build issues.
The backoff expired; registry normalization and change-history serialization
were repaired before registry publication. Retain those incidents in the
operating record; they do not count as a clean unattended observation window.
