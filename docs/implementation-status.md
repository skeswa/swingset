# v1 implementation status

Checked 2026-09-09 UTC. This records evidence against
[the implementation plan](../design/implementation-plan.md); it does not
replace its done criteria. The implementation is in the working copy and
has not been committed or pushed.

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
| WP7b | EEPro index, autoindex and round adapters; child invalidation and slow refresh clocks. Offline synthetic fixtures only. |
| WP8 | scoring.dance sitemap/index/event/round adapters and real fixtures; source-id and registry confirmations, refresh watches, expected-points checks. |
| WP9 | WDR rounds/awards adapters, validator polling, expected-403 retirement, seed script and 12 usable source overrides. Real rounds/awards fixtures. |
| WP10 | Operator commands, diagnosis, summaries, reparse, runbook, collection/removal README, issue templates and generated enum documentation. |

Some file boundaries differ from the plan's suggested layout. Event name
normalization lives in `normalize/events.py`; source-id, bib reuse and registry
confirmation live in the linking service. They use the same transactional
work and observation boundaries.

## Checks executed

Final local verification: 115 tests passed; Ruff passed; strict mypy passed
across 76 source files. These checks ran through the Nix development shell.
The final installed service completed its version-update cycle and then a
quiet cycle with zero requests and no stages.

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
| WSDC registry | Dancer 1 produced a found record and three placements. Ids 1,000,000 and 1,000,001 returned the same verified 404 error body. No sweep was started. |
| scoring.dance | Archived sitemap, recent index, event 418 and rounds 6012/6014. Full local build: 1 contest, 2 rounds, 16 entries, 6 placements, 50 callback marks and 42 final marks; 16 confirmed source-ID links with an empty registry mirror. |
| WDR | Archived one event's rounds and awards: 32 and 13 observations. Full local build: 13 contests, 32 rounds, 873 entries, 23 judges, 160 placements, 2,793 callback marks and 1,106 final marks. Repeated rounds fetch returned `NotModified`/304. |

Archived parser fixtures live under `src/swingset/sources/*/fixtures/`.
Synthetic fixtures are labeled under `tests/fixtures/sources/`. Raw source
semantics that are not established by these samples remain unverified.

The `swingset` OrbStack machine runs NixOS 25.11 in UTC with the shared checkout.
Native Python imports passed, all three timers were installed, and four
consecutive calendar-only dry cycles completed successfully. The dependency
installer needed a 120-second download timeout on the cold VM; source fetches
retain their separate 30-second timeout.

## Acceptance still pending

- The owner must create the public `skeswa/swingset` and private
  `skeswa/swingset-archive` dataset repositories and provision `HF_TOKEN`.
  Real empty-repository bootstrap, public publication, private upload and
  restoration on a fresh machine against the current public head have not run.
- Record the EEPro operator conversation before fetching its real fixtures.
  Resolve `Count` from evidence, query a complete historical event in the
  viewer, and measure one live weekend's load.
- Enable the registry deliberately, run the bootstrap sweep through normal
  cycles, obtain the comparison dump and check for missing ids. The estimate
  is about 16 hours of sweep work across cycles.
- Observe automatic finalist confirmation within seven days and a month of
  scoring.dance snapshots for nonce and Cloudflare stability.
- Obtain exact WDR UUID URLs for Swingapalooza and Jax Westie Fest. The research
  has 14 mentions but only 12 usable URLs. Parse all known events and measure
  one live weekend's conditional-response rate.
- Resolve WDR `S<n>`, `attributeGroup`, and finals bib ownership from stronger
  evidence. The adapters preserve the values and report uncertainty.
- Perform the live systemd stop-during-request acceptance check within its
  45-second limit. Automated subprocess SIGTERM recovery is covered locally.

Only the calendar is enabled in the source config, and the installed
service uses `dryRun = true`. No public data or messages to site operators
were sent. The backup timer is stopped until credentials are provisioned;
rebuilding NixOS starts enabled timers again, so stop it again after a rebuild
until setup is complete. See the [runbook](runbook.md) for activation and restore.
