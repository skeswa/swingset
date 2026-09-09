# v1 implementation status

Checked 2026-09-09 UTC. This records evidence against
[the implementation plan](../design/implementation-plan.md); it does not
replace its done criteria. The original implementation was split into 12
described Jujutsu revisions and pushed to `main` (through `2d291571`).
Publication and source acceptance fixes are tracked in GitHub issues #1–#14.

## Implemented

| Package | Code and local verification |
|---|---|
| WP0 | Python package, frozen uv dependencies, Nix flake, strict typing, CI, config, clock, structured logs. Native imports work on the Mac and arm64 NixOS. |
| WP1 | SQLite migration, ids, enums, typed observations and canonical records, writer lock, durable work, atomic input acceptance. |
| WP2 | Shared host gate, robots cache, request/byte budgets, conditional requests, retries, classification, compressed raw archive and extracts. Mock-transport tests cover failure and pause behavior. |
| WP3 | Discovery, watch lifecycle, cycle budgets, calendar extraction, mapping, transactional projection, interruption recovery, pause/resume. A quiet calendar cycle does no fetch or stage work. |
| WP4 | Reusable NixOS module, CLI package, cycle/backup/summary timers, service hardening, graceful stop handling. OrbStack NixOS machine installed and running calendar-only dry cycles. |
| WP5 | Explicit Arrow schemas, invariant checks, suppressions, review queue, changelog, immutable candidates, dataset card, Hub adapter, publication reconciliation, complete checkpoints, locked restore and GC. Offline tests exercise publication failure boundaries and artifact closure. |
| WP6 | Registry parser and projection, verified missing-id classifier, durable sweep/probe cursors, daily trickle, archived dump cross-check and replay. Real found/miss fixtures and fake-clock cursor tests. |
| WP7a | Name/division normalization, nickname seed, canonical contest projection, event/role assignment, manual overrides, judge restrictions, identity candidates and invalidation. |
| WP7b | EEPro index, autoindex and round adapters; child invalidation and slow refresh clocks. Real Summer Hummer fixtures, named judges, paired finals bibs, date ranges, and Count ranking verified. |
| WP8 | scoring.dance sitemap/index/event/round adapters and real fixtures; source-id and registry confirmations, refresh watches, expected-points checks. |
| WP9 | WDR rounds/awards adapters, validator polling, expected-403 retirement, seed script and 12 usable source overrides. Real rounds/awards fixtures. |
| WP10 | Operator commands, diagnosis, summaries, reparse, runbook, collection/removal README, issue templates and generated enum documentation. |

Some file boundaries differ from the plan's suggested layout. Event name
normalization lives in `normalize/events.py`; source-id, bib reuse and registry
confirmation live in the linking service. They use the same transactional
work and observation boundaries.

## Checks executed

Release verification: 153 tests passed; Ruff passed; strict mypy passed
across 76 source files. These checks ran through the Nix development shell.
The installed writer completed its version-update cycle, controlled
publication, private backup, both recovery drills, and the first scheduled
jobs. Continued unattended-operation windows remain acceptance work.

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

| Source | Observed result |
|---|---|
| WSDC calendar | Full archived body produces 172 observations and 169 deduplicated events. A second cycle was quiet. |
| WSDC registry | Dancer 1 produced a found record and three placements. Ids 1,000,000 and 1,000,001 returned the same verified 404 error body. The comparison dump has been archived and verified against isolated state (27,039 IDs). The production sweep is prepared but has not completed. |
| scoring.dance | Archived sitemap, recent index, event 418 and all 12 rounds. Full local build: 6 contests, 12 rounds, 202 entries, 62 placements, 777 callback marks and 420 final marks. Bib identity is scoped by contest and role. |
| WDR | Archived one event's rounds and awards: 32 and 13 observations. Full local build: 13 contests, 32 rounds, 873 entries, 23 judges, 160 placements, 2,793 callback marks and 1,106 final marks. Repeated rounds fetch returned `NotModified`/304. |

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
  and packed archive formats restored against the public head. Continue to
  observe the scheduled writer, backup, summary, and quiet-cycle behavior under
  issues #9 and #1.
- The owner reported EEPro permission received on 2026-09-08; see its source
  playbook. Real Summer Hummer fixtures, `Count` semantics, and a complete local
  event build passed. Results publication is held for the contest-key collision
  fix, and the live-weekend measurement remains pending under issue #12.
- Run the prepared registry bootstrap sweep through normal cycles
  and compare the completed mirror with the already archived 27,039-ID dump.
  It is the next operating step; no production completion or coverage claim
  exists yet.
- Observe automatic finalist confirmation within seven days and a month of
  scoring.dance snapshots for nonce and Cloudflare stability.
- Obtain the exact Jax Westie Fest 2026 scores UUID. Swingapalooza is verified;
  all 13 known event URLs now have complete rounds and awards captures. Measure
  one live weekend's conditional-response rate.
- Resolve WDR `S<n>` and finals bib ownership from stronger evidence. The NASDE
  attribute-group legend is verified; other groups remain unverified. The
  adapters preserve unknown values and withhold unsupported canonical claims.

The live systemd stop-during-request check passed on 2026-09-09. Run
`run_20260909T141539Z` finished its single calendar request and stopped in
1.35 seconds. The next dry cycle succeeded, SQLite integrity and foreign-key
checks passed, and no work was left pending. See closed issue #6.

The deployed public dataset remains calendar-only while results publication is
held for the contest-key collision fix. The selected writer uses `dryRun =
false`. The current reviewed calendar head is
`957b9e266b549d729ef978b3d0ddcc738c7d901f`; an unchanged repeat made no commit,
and both the remote DuckDB query and events viewer passed. Private archive
commits `5ad249109d6dc866b9d9d4432ecac2fea8126708` (legacy flat) and
`c691a760015698247d157c2c2cf738fc712ea53e` (packed) passed fresh-machine
restore. Initial scheduled jobs passed, while issue #9 remains open for the
ongoing operating observation. See the [runbook](runbook.md) for the active
handoff and restore evidence.
