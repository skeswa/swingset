# Implementation plan for v1

Status: implementation underway, 2026-09-09. Owner: Sandile Keswa.

See [implementation status](../docs/implementation-status.md) for implemented
code, executed checks, and acceptance work still pending. Done criteria below
remain acceptance requirements; code coverage alone does not satisfy them.

This is the order of work for the first version of the pipeline. It
refines [milestones](milestones.md) and [scraping plan](scraping-plan.md).
The contract owners in section 3 define how the pipeline works. Facts
about this Mac were checked on 2026-09-08. Facts we could not check are
marked **unverified**.

## 1. What v1 is

v1 is milestones M0 through M3b, running under systemd timers in a
NixOS virtual machine on the development Mac, publishing to
`skeswa/swingset` on Hugging Face. The same NixOS module installs
unchanged on the production box later.

| In v1 | Milestone |
|---|---|
| Flake, NixOS module, CLI, config, SQLite state, fetch layer with politeness and archive, watches and scheduler, cycle timer, backup and restore | M0 |
| WSDC calendar as the root of discovery | M0 |
| Registry mirror: bootstrap sweep, dump cross-check, weekly probe, trickle, post-event confirmation; `dancers` and `registry_placements` | M1 |
| Canonical model through `placements`; name normalization; EEPro discovery and parsers; name-based linking; `identity_links`, `link_candidates`, `review_queue`; overrides | M2 |
| scoring.dance parsers; source-id links; registry confirmation loop; `points_matches_expected` | M3 |
| World Dance Registry parsers fed by `overrides/source_urls.csv` | M3b |
| Build with invariants, suppression, changelog, manifest; publish with atomic commits and the dataset card | M0 to M3 |

Out of v1, with what v1 keeps so nothing has to change later:

| Deferred | Why | Kept in v1 |
|---|---|---|
| danceconvention.net (M4) | needs `node` evaluation, PDF parsing, byte budgets; 19 events a year | `daily_byte_budget` in the gate; `derived blob` slot in the archive; `node` in the devshell |
| Wayback transport and backfill (M6) | weeks of low-priority fetching; nothing else depends on it | `archive_url` and `via` columns; `backfill` watch state; `web.archive.org` in `hosts.toml` |
| Event-site link scan | discovery nicety; overrides cover 12 of 14 known WDR events | `site` watch kind reserved |
| Heats, generic long-tail adapters, LLM draft tool (M5) | data is thin or manual | `heats` and `judges` tables published, `judges` filled, `heats` empty |
| Splink weight fitting | needs scoring.dance data first | hand-set weights in `link/weights.toml`; `link_candidates` keeps every signal so fitting is offline later |
| Summary webhook | destination undecided | `swingset summary` writes to the journal |
| Per-source adaptive live floor | decision 13 says later | fixed 15 min floor |

## 2. Environment

### 2.1 The Mac (verified 2026-09-08)

- Apple Silicon (arm64), 48 GB RAM, 15 cores, macOS 25.5.
- Determinate Nix 3.22.2 with `nix-command` and `flakes` enabled;
  nix-darwin installed. `nixos-test` and `apple-virt` are in
  `system-features`. No remote Linux builder is configured
  (`/etc/nix/machines` does not exist).
- OrbStack 2.2.3 with the `nixos` distro available (`unstable` and
  `25.11`, default `25.11`), arm64 and amd64.
- Not installed on the host: `uv`, `node`, `qemu`, `nixos-rebuild`,
  `limactl`, UTM. System Python is 3.9.6. `jj` and `git` are present.

### 2.2 The VM: an OrbStack NixOS machine

Decision: v1 runs in an OrbStack NixOS 25.11 machine named `swingset`.

Why: it is already installed; it is real NixOS with systemd, so the
module and timers run as they will on the box; it builds
`aarch64-linux` derivations natively, so no remote builder is needed;
the Mac's home directory is visible inside the machine at the same path
(verified 2026-09-09); it has
network access; `orb` gives a shell and `journalctl` in one command.

```
orb create --cpus 4 --memory 8G nixos:25.11 swingset
orb -m swingset                                # shell inside
sudo nixos-rebuild switch --flake /Users/skeswa/repos/skeswa/swingset#orb --impure
```

`--impure` is needed because `nix/hosts/orb.nix` imports the
machine-generated `/etc/nixos/configuration.nix`, which imports OrbStack
integration and also supplies container boot, networking, users, and
certificates. Importing only `orbstack.nix` omitted required settings. The
full configuration import and switch were verified on 2026-09-09.

The restore test in M0's done criteria uses a second, fresh machine
(`orb create nixos:25.11 swingset-restore`), never the first one.

Rejected: `nixos-rebuild build-vm` (qemu) needs a Linux builder and
qemu on the host, neither present; UTM and lima are not installed.
The OrbStack machine can serve as a remote builder later if a
boot-from-scratch qemu test is ever wanted.

Time zone of the machine: UTC. This answers open question 3 for v1.

### 2.3 Toolchain

Per [technology](technology.md): nix owns Python 3.12, `uv`, and
`node`; `uv` owns Python dependencies from `pyproject.toml` and
`uv.lock`.

Decisions the design left open:

| Concern | Decision |
|---|---|
| nixpkgs pin | `nixos-25.11` branch, matching the OrbStack image, so the machine and the flake share a store |
| Python | `pkgs.python312`; `uv` is told never to download its own Python (`python-downloads = "never"` in `uv.toml`, `UV_PYTHON` set to the nix interpreter) |
| Where the venv lives in the service | `UV_PROJECT_ENVIRONMENT=/var/lib/swingset/venv`, `UV_CACHE_DIR=/var/lib/swingset/uv-cache`; `ExecStartPre` runs `uv sync --frozen --no-dev` so a rebuild with a new lock refreshes it |
| Native wheels on NixOS | manylinux wheels (`pyarrow`, `duckdb`, `scipy`, `numpy`, `rapidfuzz`, `selectolax`) need `libstdc++` and `zlib` from the system. The devshell and the unit set `LD_LIBRARY_PATH` to `${stdenv.cc.cc.lib}/lib:${zlib}/lib`. Verified by WP0's native import smoke test on 2026-09-09. Fallback, in order: take those packages from nixpkgs and give uv a venv with `--system-site-packages`; then, if still broken, drop uv and use `python312.withPackages` for everything |
| Type checking | `mypy --strict` from the first commit, with `ignore_missing_imports` for libraries without stubs (`protego`, `nameparser`, `selectolax`) listed in `pyproject.toml` |
| Test clock | every module that reads time takes a `Clock` protocol (`now()`, `sleep()`); tests pass a fake. No `freezegun` |

Runtime dependencies in v1: `httpx`, `protego`, `selectolax`,
`pyarrow>=21`, `duckdb`, `rapidfuzz`, `scipy`, `nameparser`,
`huggingface_hub`. Development: `pytest`, `respx`, `ruff`, `mypy`.
Added in v1.1: `pdfplumber`, `splink`.

### 2.4 Daily loop

```
# on the Mac
nix develop                    # python, uv, node, ruff, mypy in PATH
uv run pytest -q
uv run swingset cycle --dry-run --state ./tmp/state   # never publishes

# in the machine
orb -m swingset
sudo nixos-rebuild switch --flake .#orb --impure
systemctl list-timers 'swingset-*'
journalctl -u swingset-cycle -f
sudo -u swingset swingset doctor
```

Commit with `jj` as [AGENTS.md](../AGENTS.md) says. One work package is
one or a few commits, each leaving tests green.

## 3. Contracts used by the work packages

This plan owns v1 scope, environment choices, work order, and acceptance
criteria. The following documents own the implementation contracts;
update them in the same change when a package changes a contract.

| Contract | Owner |
|---|---|
| Observation ownership, matching map, projection, findings | [Architecture](architecture.md) |
| SQLite schema, invalidation, durable work, state directory | [Local state](state.md) |
| Extract, parse, source interfaces, fixtures | [Parsing](parsing.md) |
| Response classification and host gate | [Fetching](fetching.md) |
| Watch policy and discovery | [Scheduling](scheduling.md) |
| Build inputs, review queue, immutable output | [Build](build.md) |
| Candidate identity, recovery, baseline promotion | [Publishing](publishing.md) |
| Cycle, locks, pause, backup and restore | [Operations](operations.md) |
| Modules and files | [Repository layout](repository-layout.md) |

## 4. Work packages

Sizes: S is a session, M is two or three, L is a weekend or more. Each
package ends with tests green and a reviewable change; commit when asked. Human tasks that must
happen alongside are in section 5.

### WP0. Skeleton and toolchain (S)

Deliverables: `flake.nix` with `devShells.default` and
`packages.default`; `pyproject.toml` with the dependency list in 2.3,
`ruff`, `mypy`, `pytest` config; `uv.lock`; `uv.toml`; `src/swingset/cli.py`
with `--version` and subcommand stubs; `config.py` reading
`hosts.toml` and `sources.toml` into frozen dataclasses with the
defaults from [fetching](fetching.md#politeness-rules); `clock.py`;
`log.py` (structured, key=value, to stderr); `.github/workflows/ci.yml`
running `ruff`, `mypy`, `pytest`.

`config/hosts.toml` is written from the playbooks' section 6 and
carries a comment naming the playbook line each value comes from.

Done when: `nix develop` then `uv run pytest` passes on the Mac; the
OrbStack machine exists; inside it `nix develop` works from the repo
path and `python -c "import pyarrow, duckdb, scipy, rapidfuzz, selectolax, httpx"`
succeeds (this answers the native-wheel question in 2.3);
`swingset doctor` prints the effective host table.

### WP1. State, ids, enums, records (S)

Deliverables: `state/db.py` (open, migrate, `run_id`, lock file,
transactions); `migrations/0001_init.sql` with the tables in
[local state](state.md#sqlite-schema); `model/ids.py` implementing every id in
[data model](data-model.md#identifiers) with tests for slugging,
`-2` suffixes, and the name-form entry id; `model/enums.py` as
`StrEnum`s, and `docs/enums.md` generated from them by
`swingset enums --write`; `model/observations.py` and
`model/canonical.py` (the two sides of the observation boundary);
`state/work.py` and `state/findings.py`.

Done when: a fresh database migrates, `swingset doctor` shows schema
version 1, and id tests cover every example in the data model.
Input-acceptance tests prove that recording a changed digest and
enqueueing its work cannot commit separately, including a restart
before any queued unit runs.

### WP2. Fetch core (M)

Deliverables: `fetch/client.py`, `fetch/politeness.py`,
`fetch/robots.py`, `fetch/archive.py` as specified in
[scraping plan](scraping-plan.md#phase-0-fetch-core-milestone-m0)
and [fetching](fetching.md): one in flight per host, `next_allowed_at`,
`Crawl-delay`, `Retry-After` (seconds and date), request and byte
budgets per day, `fetch/classify.py` following
[response classification](fetching.md#response-classification), with the
pause rules keyed on classification (`Throttled` doubling from 15 min
to 24 h; `Blocked` 24 h and run failure; `ExpectedUnavailable` and
`Gone` touch only the watch), 3 retries with full-jitter
backoff from 10 s, redirects as separate gated requests (3 hops),
gzip only, no cookies, fixed User-Agent with the package version,
robots cached 24 h with 4xx unrestricted and 5xx disallow-all and the
`swingset` group winning, content-addressed blobs gzip-compressed,
`snapshots` rows, derived-blob store keyed by `extract_sha256`.

Change detection in the order [fetching](fetching.md#change-detection)
gives, driven by the page kind's `change_mode`. Unchanged bodies are
discarded. `ExtractError` archives the body with `extract_status =
failed`.

Tests: `respx` routes for 200, 304, 429 with and without
`Retry-After`, 403, 5xx, redirect chains, gzip and identity bodies;
a fake clock proving the 5 s gap, `Crawl-delay` precedence, budget
stops, and pause doubling; robots precedence cases; archive dedup;
classification: two watches on one host where one returns 403 that
its page kind expects (never had a 200) and the other returns 200,
proving the host is not paused and the second watch keeps polling,
then a 403 on the second watch pausing the host; a challenge body on
a 200 classified `Blocked`.

Done when: `swingset fetch-one <url> --kind wsdc_calendar.events`
fetches through the gate into the archive and a second call is a
no-op by fingerprint.

### WP3. Watches, scheduler, cycle, calendar (M)

Deliverables: `schedule/watches.py` (upsert by `watch_id`, state
transitions, `gone` after 3 404s over 3 days); `schedule/policy.py`
implementing the state table in [scheduling](scheduling.md#watch-states-and-intervals)
with live-window padding, jitter, doubling, per-host overrides from
`hosts.toml`, index intervals by weekday and weekend, and the
priority order in [scheduling](scheduling.md#work-order);
`schedule/cycle.py` implementing [cycle order](operations.md#cycle),
wall-clock budgets, [interruption recovery](operations.md#interruption-and-recovery),
and run summaries. `state/work.py` accepts captured file changes and
drains durable work; `swingset pause` and `swingset resume` follow
[operator command semantics](operations.md#locks-and-operator-commands).
The observation store, `pending_work`, and
`project/writer.py` implement transactional scope replacement, preserving
`first_seen_at`, deleting obsolete rows, and recording conflicts.
The first projections are `project/events.py`
(calendar observations to `events`), index observations to
`source_events`, and `project_map` producing `source_event_map`,
with the writer re-projecting every event whose membership changed
when the map changes; `schedule/discover.py` steps 1, 3, 4, and 5 of
[scheduling](scheduling.md#discovery) (calendar, matching, event
watches, `source_urls.csv`), where matching is the map projection,
ambiguous matches are computed review items, and
`overrides/event_aliases.csv` (`source, source_ref, event_id, note`)
and `overrides/source_urls.csv` are map inputs. The cycle runner
also carries [publication reconciliation](publishing.md#candidate-and-baseline)
as a no-op until WP5 fills it in.

Establishing the parser to observation to projection boundary here,
on the simplest source, is deliberate: WP6 and WP7a add projections,
not new mechanisms.

`sources/wsdc_calendar/`: `wsdc_calendar.events` page kind, ported
from `research/build_events.py`, `change_mode = "extract"` with the
extract covering row class, site URL, and flag code as the playbook
says. Fixture: one archived calendar body.

`swingset doctor` prints hosts, budgets, pauses, watches by source and
state, last run. `swingset summary` prints the daily digest to the
journal.

Done when: `swingset cycle --dry-run` on the Mac fetches the calendar
once, stores `CalendarRow` observations, projects `events` rows, and a
second cycle within 24 h fetches nothing and runs no stage; re-parsing
the same snapshot changes no row and no `first_seen_at`; editing
`event_aliases.csv` alone makes `project` run on the next cycle; the
fake-clock test walks one event through `dormant`, `upcoming`,
`live`, `cooling`, `archived`; the crash-injection harness of
[interruption recovery](operations.md#interruption-and-recovery) passes for fetch, parse, and project at every
transaction boundary and on SIGTERM; a paused cycle
(`swingset pause --all`) makes no request but still projects a
changed alias; `swingset resume` after a simulated week checks each
overdue watch once and then returns to schedule. Extractor-only bumps
re-extract archived bodies; failed parses complete with evidence rather
than spin; a full fetch batch leaves downstream work that is drained
before another batch. Pause-under-lock and timeout cases from operations
also pass.

### WP4. NixOS module and the machine (S). Ends M0 with WP5's restore.

Deliverables: `nix/module.nix` with options `enable`, `package`,
`stateDir` (default `/var/lib/swingset`), `environmentFile`,
`overridesDir`, `cycleBudget` (default `12m`), `dryRun` (default
`true`); a `swingset` user and group; `swingset-cycle.service` and
`.timer` (every 15 min, `RandomizedDelaySec=120`, `Persistent=true`),
`swingset-backup.timer` (Mon to Thu 04:00; Fri to Sun 04:00, 12:00,
20:00), `swingset-summary.timer` (08:00); a flock in the state
directory shared by writers, with the caller-specific waits and backup
retry policy in [operations](operations.md#locks-and-operator-commands);
`ExecStartPre` running `uv sync --frozen --no-dev` into the state directory; hardening
(`DynamicUser=false`, `ProtectSystem=strict`, `ReadWritePaths`
limited to the state directory, `PrivateTmp`, no new privileges),
`KillMode=mixed` and `TimeoutStopSec=45` so a stop during a cycle is
the graceful stop of [interruption recovery](operations.md#interruption-and-recovery).
`nix/hosts/orb.nix` and `nixosConfigurations.orb` in the flake.
`docs/runbook.md` sections: create the machine, rebuild, read logs,
set the token, rotate the token, pause and resume (the command, the
timer, and active service), disable a source, stop the machine
safely mid-cycle, restore verification, and the remote-ahead recovery
report. Document that timer stop alone leaves an active cycle running.

Done when: on the machine, `systemctl list-timers` shows the three
timers, four consecutive cycles run against the calendar only,
`journalctl` shows each cycle's one-line summary, and
`systemctl stop swingset-cycle` during a cycle exits 0 within
45 s with a `stopped = true` run file and a clean next cycle.

### WP5. Build, publish, backup, restore (M). Completes M0.

Deliverables: `model/schema.py` with a PyArrow schema per published
table; `build/` implementing [build inputs and immutable contents](build.md),
invariants, suppression, review, changelog, and manifest; `publish/`
implementing [candidate and baseline](publishing.md#candidate-and-baseline),
Hub commits, and the dataset card; `backup/` implementing the complete
checkpoint and activation protocol in
[backup and restore](operations.md#backup-and-restore). Runtime bootstrap
must verify empty-repo handling in the Hub adapter.

Tests: each invariant has a failing fixture; suppression nulls the
specified fields; changelog detects add, remove, and update. All
[build acceptance cases](build.md#acceptance-cases),
[publication recovery cases](publishing.md#publication-acceptance-cases),
and restore cases in operations pass offline. In particular:

- A vocabulary-only correction builds and publishes with links and
  snapshots unchanged; a findings-only cross-check also publishes.
- Build suppression B in a dry run, publish C, then return to B.
  Rebuild B against C and preserve C's changelog; reuse is keyed by
  both build inputs and baseline.
- Failure before or after each publication marker and remote commit
  yields one remote commit per candidate. Lost responses and requests
  that never reach the remote are separate cases.
- A pending dry run may read head but never writes a commit. A saved
  publication receipt permits local promotion with no request.
- Restore a checkpoint containing a pending candidate at each failure
  boundary; preserve its extracts and captured inputs as well as raw
  blobs. Missing artifacts fail validation. A public head ahead of the
  checkpoint keeps publishing disabled and leaves remote history intact.
- A backup overlapping a cycle waits and eventually checkpoints;
  interrupted or failed backups are retried and never logged as success.
- Unchanged semantic content with fresh run metadata causes no extra
  commit; a fully quiet cycle makes no network call. GC removes only
  eligible unreferenced artifacts.

Done when: the owner creates the two Hub repos, provisions `HF_TOKEN`,
sets `dryRun = false`, and the next cycle publishes `events` plus every
empty table and the card. With the original writer stopped, a fresh
`swingset-restore` machine restores a checkpoint at the current public
head, passes artifact and remote verification, and reports the same
pipeline state in `doctor`. Resume only one writer. That is M0.

### WP6. Registry mirror (M). Ends M1.

Deliverables: `sources/wsdc_registry/` with the `wsdc_registry.dancer`
page kind (POST `num=<id>`, `change_mode = "body_hash"` after
dropping nothing, since the JSON has no nonce) emitting `dancers` and
`registry_placements` records with the quirks in the playbook
(`placements` dict or list; `wscid` is the number; `dancer.id` kept as
`registry_internal_id`) as one `DancerLookup` observation per id,
projected by `project/registry.py`. Registry watch policy in `policy()`:
bootstrap sweep from `cursors.registry_sweep_next` at a 2 s gap,
lowest priority, daily budget 20,000 during the sweep then 1,500;
weekly probe above the highest id until 20 consecutive misses; trickle
of 100 a day for dancers older than 365 days; post-event confirmation
refresh (daily, 30 days) wired in WP8. `swingset sweep --start 1`
seeds the cursor. `swingset registry-crosscheck <dump.json>`
archives the dump as a blob (source `crosscheck`, `via = manual`),
diffs it against the mirror, and writes one `registry_diff` finding
per discrepancy with the dump row and the mirror row as evidence; the
dump is never written into any table, and the check can be re-run
from the blob after a restore.

Every lookup is classified as one of three outcomes. `Found`: JSON
with `type = "dancer"` whose `wscid` equals the requested id.
`NotFound`: the verified miss shape. `Invalid`: anything else,
including a `wscid` mismatch, an HTTP 200 error envelope, or a
changed schema. Only `NotFound` advances the probe's miss counter and
marks a sweep id as absent. `Invalid` records the snapshot, leaves
the cursor and the miss counter where they were, schedules a retry
with backoff, and opens an `invalid_response` finding; an `Invalid`
rate above 10 percent in a run fails the run. The miss shape was verified on 2026-09-09 using ids 1,000,000 and
1,000,001: HTTP 404 with the exact archived HTML error body. The
[registry playbook](../docs/sources/wsdc-registry.md#9-quirks)
records the digest. A changed error body remains `Invalid`; it does not
advance a cursor. Dancer id 1 supplies the real `Found` fixture.

Division codes: the enum starts with the codes in
[sources](sources.md#registry-json-shape-lookup2020find-trimmed); an
unknown code is emitted as `other` with an `unknown_enum` finding
holding the code and the snapshot, so the sweep tells us the full set
without failing.

Tests: fixtures for a dancer with placements, one with empty
placements, one verified miss, and one invalid envelope; sweep cursor
advance on `Found` and `NotFound`, no advance on `Invalid`; probe
termination only on 20 verified misses on a fake clock; cross-check
findings survive a build and a restore; a cross-check-only cycle (no
fetch, no observation or link change) makes build due through the
`findings` revision and publishes the new review items in one commit
without running link.

Done when: the sweep has run to completion inside normal cycles on
the machine (about 16 hours of wall time across cycles), the dump
cross-check reports no missing ids, and the viewer shows `dancers`
and `registry_placements`. That is M1.

### WP7a. Canonical model, normalization, linking core (L)

Deliverables: `normalize/names.py` implementing the six steps in
[identity linking](identity-linking.md#name-normalization) and
`overrides/nicknames.csv` (seed of about 100 common pairs);
`normalize/divisions.py` mapping printed contest names to `division`,
`age_division`, `contest_type`, `partner_mode`, `dance_style`,
`combined_from`, with a table-driven vocabulary and tests for every
EEPro and scoring.dance name seen in `research/`; `normalize/events.py`
(series slug, name matching used by discovery); `project/contests.py`,
the [event projection](architecture.md#observations-and-projections)
that turns round, event,
and index observations into `contests`, `rounds`, `entries`,
`judges`, `callback_marks`, `callbacks`, `final_marks`, and
`placements`, computing `rounds_danced`, `best_round`, `entry_count`,
and `promoted_count` from the union of the event's observations and
emitting `conflict` findings under the precedence rule.

`link/`: `candidates.py` (blocking on last-token first letter, exact,
nickname, Jaro-Winkler 0.92, token-set 90); `score.py` with the
signals in [identity linking](identity-linking.md#scoring-and-methods)
and weights from `weights.toml`; `assign.py` with
`linear_sum_assignment` per event and role; `confirm.py` for
`bib_reuse` and, from WP8, `registry_placement` and `source_id`;
`overrides.py` for `identity_overrides.csv` (`entry_id, wsdc_id or
NONE, reason, author, date`). Output: `identity_links` and
`link_candidates` rows, `entries.wsdc_id` set only for `confirmed`
and `probable`, `changelog` reasons `link_upgraded` and
`link_downgraded` derived at build. Judges are linked with the
`is_pro` or All-Star and Champion candidate restriction.

Tests: normalization golden cases (accents, particles, suffixes,
nicknames); division vocabulary table; projection: an entry present
in prelims and finals survives a re-parse of finals that drops it and
loses `final` from `rounds_danced`, and is deleted only when no
observation names it; a judge across two contests is one `judges`
row; two round pages disagreeing on a name spelling produce one row
under the later snapshot and one `conflict` finding; an event page
and a round page disagreeing on a round name resolve to the round
page; re-projecting unchanged observations changes nothing; with
archived bodies unchanged, adding an `event_aliases.csv` row that
moves a source event from event A to event B re-projects both in one
transaction, leaving A without its contests and B with them under
recomputed ids, and the changelog delta shows the move.
Linking: assignment resolves the two same-name case; overrides win;
determinism (same input, same links); a weights-only edit re-links all
events. Kill after accepting the weights and after each event commit,
then resume without another edit. A second weights edit during partial
work also reaches every event. A registry addition reaches previously
unmatched entries. These complete the [invalidation cases](state.md#acceptance-cases).

### WP7b. EEPro (M). Ends M2 with WP7a.

Deliverables: `sources/eepro/` with `eepro.index`
(`event.php`, `change_mode = "extract"` on the row list),
`eepro.autoindex` (Apache listing; compares `Last modified` and
`Size` text for equality and marks child round watches due),
`eepro.round` (header-driven columns, prelims and finals shapes,
`Y/A1/A2/A3/N` to the shared enum, `255/720` bibs, `Marks Sorted`),
discovery step 2 of [scheduling](scheduling.md#discovery) for this
source, the round-page slow clock from the playbook
(`round_live_interval = 12h`, `round_cooling_interval = 24h`).
Fixtures: one past event's autoindex, one J&J prelims, one J&J finals,
one couples finals, taken with `swingset fetch-one` (about six
requests, made once, after the operator conversation in section 5).

Answer the `Count` column question from the first fixture and record
it in the playbook.

Done when: one full past EEPro event is queryable end to end in the
viewer with `link_status` populated, `review_queue` lists its
ambiguous names, and the machine has polled one live EEPro weekend
within the playbook's load estimate (check `runs/*.json` per host).
That is M2.

### WP8. scoring.dance (M). Ends M3.

Deliverables: `sources/scoringdance/` with `scoringdance.sitemap`
(ids only), `scoringdance.recent`, `scoringdance.event`
(`change_mode = "extract"` on the ordered contest and round links;
on change, every round watch of the event is made due),
`scoringdance.round` (cells plus `data-wsdc`, `data-state`, judge
`title`, chief-judge marker), `If-Modified-Since` always sent,
challenge detection (`cf-chl`, `Just a moment`) pausing the host and
failing the run, round clocks `4h` live and `24h` cooling, and the
budget rule that round refreshes wait before event polls.

Linking: `source_id` links from `data-wsdc` (`confirmed`, 1.0);
`registry_placement` confirmation after the registry posts, which
also drives the post-event refresh watches from WP6;
`points_matches_expected` from [WSDC rules](wsdc-rules.md) using the
prelims `entry_count` per role. The nonce check (raw hash differs,
fingerprint does not) is a counter in `runs/*.json`.

Fixtures: sitemap, recent, one event page, one prelims round, one
finals round.

Done when: an event's finalists show `confirmed` links within seven
days of the event without human action, and one month of snapshots
has answered the nonce and Cloudflare stability questions in the
playbook. That is M3.

### WP9. World Dance Registry (S). Ends M3b.

Deliverables: `sources/wdr/` with `wdr.rounds` and `wdr.awards` on
`routeInfo.json` (`change_mode = "validators"`), the cell type codes
from the playbook, redacted rows emitted with `name_raw = "***"` and
null marks, `roundName` split on ` - `, and `expected_statuses`
returning `{403}` while the watch has never had a 200 so the fetch
layer classifies it `ExpectedUnavailable` (daily for 30 days, then
`gone`) instead of pausing the host ([response classification](fetching.md#response-classification)). Discovery from
`overrides/source_urls.csv` (`event_id, source, kind, url, parser,
notes`), seeded by a one-off script from `research/results-sources.csv`
(14 mentions, of which 12 contain usable UUID URLs; the script lives in
`research/` and is run once). Swingapalooza and Jax Westie Fest have
only host-root links and still need exact event URLs.

Answer `S<n>`, the finals bib, and `attributeGroup` from the first
fixtures and record them in the playbook.

Tests: an event supplied only through `source_urls.csv` gets a
watch whose `source_ref` is `wdr:<uuid>`, a map entry with
`match_method = override`, and canonical rows under that `event_id`;
changing the row's `event_id` with archived bodies unchanged makes
`project` due and moves every row to the new event in one
transaction; a row for an event that never publishes ends as `gone`
after 30 daily `ExpectedUnavailable` polls without pausing the host.

Done when: the 14 known events parse and a live WDR weekend shows
almost all polls as 304 in `runs/*.json`. That is M3b.

### WP10. Hardening and documentation (S)

- `swingset doctor` complete: per-source last success, operator and
  host pauses with their reasons, budget use, watches by state,
  pending candidate, pending work, restore status, last successful
  backup, review queue size, and last publish SHA.
- Parse failure rate above 10 percent for a source fails the run;
  a host paused for a block or challenge fails the run and leads the summary.
- `swingset reparse --kind <kind>` and `--since` after a version bump.
- `docs/runbook.md` complete; `README.md` rewritten as the page the
  User-Agent points at: what we collect, why, how to opt out, how to
  ask us to slow down, the issue templates.
- The dataset card's coverage table and gaps list (the four
  `not_found` events and the Facebook-only ones).
- Check that implemented behavior matches the contract owners in
  section 3. Move answered source questions into the playbooks;
  architecture and state contracts are already recorded there.

## 5. Human tasks alongside the code

| Task | Before | Who |
|---|---|---|
| Create `skeswa/swingset` (public) and `skeswa/swingset-archive` (private) on the Hub; make a write token; put it in the machine's environment file | WP5 done criteria | owner |
| The EEPro operator conversation from [scraping plan](scraping-plan.md#operator-conversation-before-phase-2), recorded in the playbook's section 1 | first EEPro fetch (WP7b fixtures) | owner |
| Fill in the scoring.dance operator relationship in its playbook; decide open question 1 (paid API) | WP8 live polling | owner |
| Review the first `review_queue` and add override rows | after WP7b | owner |
| Download the mechstack dump once for the cross-check | WP6 | owner or script |

If the EEPro conversation is pending when WP7a is done, do WP8 before
WP7b; the two are independent, and scoring.dance needs no permission
beyond its robots.txt.

## 6. Order and rough calendar

Strict order: WP0, WP1, WP2, WP3, WP4, WP5. Then WP6 and WP7a in
either order or interleaved. Then WP7b, WP8, WP9 in any order (WP8
first if EEPro is waiting). WP10 last. The registry sweep in WP6 takes
about 16 hours of machine time and runs unattended inside cycles, so
start it as soon as WP6 lands.

A plausible sequence at the project's weekend cadence:

| Weekend | Packages | Result |
|---|---|---|
| 1 | WP0, WP1, WP2 | fetch layer tested offline; machine exists |
| 2 | WP3, WP4, WP5 | M0: timers on the machine, first publish, restore proven |
| 3 | WP6, start WP7a | M1: registry tables published; sweep running |
| 4 | WP7a, WP7b | M2: first EEPro event end to end |
| 5 | WP8 | M3: confirmed links from scoring.dance |
| 6 | WP9, WP10 | M3b and cleanup; v1 declared |

These are estimates, not commitments.

## 7. Definition of done for v1

- The machine has run unattended for two consecutive event weekends
  with no paused host and no failed run.
- `skeswa/swingset` shows every table in the viewer; `placements`
  joins to `events` and `entries` with the DuckDB example in the card.
- At least one event from each of EEPro, scoring.dance, and WDR is
  published with rounds, marks, and placements.
- `dancers` holds every id from the sweep; the dump cross-check is
  clean or its diffs are in the review queue.
- `confirmed` links exist for a scoring.dance event's finalists via
  both `source_id` and `registry_placement`.
- Restore at a matching public head reproduces the checkpoint
  state. Missing artifacts and a remote-ahead head keep publishing
  disabled with an actionable report.
- `pytest` passes offline, including the override-only cycle, the
  crash-injection harness at every boundary, and SIGTERM at every
  boundary; CI is green; `mypy --strict` is clean.
- The machine has been stopped mid-cycle and resumed at least once
  during a live weekend with no duplicate rows, no lost snapshot, and
  no extra Hub commit.
- Every load number in `runs/*.json` for a live weekend is at or under
  the playbook's estimate for that host.

## 8. Things to verify during v1

| Item | Package |
|---|---|
| OrbStack exposes the Mac home directory at the same path; `/etc/nixos/orbstack.nix` imports under `--impure` | WP0, WP4 |
| manylinux wheels import on NixOS with `LD_LIBRARY_PATH` | WP0 |
| Calendar fingerprint stable across a week | WP3 |
| Registry miss response; full division code set; `adv_sliding` meaning | WP6 |
| EEPro `Count` column; operator's answer on autoindex; `/results/<year>/` existence | WP7b |
| scoring.dance nonce check; Cloudflare stability at 15 min | WP8 |
| WDR `S<n>`, finals bib, `attributeGroup` | WP9 |
| Timing from scoring to posting per platform, measured from snapshots | WP8, WP9 |

## 9. Risks

- **Native wheels do not load on NixOS.** Caught in WP0's smoke test;
  the fallbacks in 2.3 cost a few hours, not a redesign.
- **OrbStack quirks** (custom kernel, its own networking) differ from
  the production box. The module carries no OrbStack-specific option;
  `nix/hosts/orb.nix` is the only file that knows. A second host file
  for the box is a copy with a different import.
- **The EEPro conversation delays M2.** WP8 goes first; the data
  model does not care which source lands first.
- **Cloudflare challenges scoring.dance.** The host pauses, the run
  fails loudly, and the paid API question becomes urgent; WDR and
  EEPro keep publishing.
- **The registry sweep is noticed.** It is 1 request per 2 s to a
  lookup endpoint that other scrapers hit at 3 per second; the
  User-Agent names us, and `enabled = false` stops it in one cycle.
- **Scope creep toward DCN and backfill.** Both are v1.1. The columns
  they need already exist, so nothing in v1 is thrown away.
