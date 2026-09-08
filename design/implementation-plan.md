# Implementation plan for v1

Status: draft v0.1, 2026-09-08. Owner: Sandile Keswa.

This is the order of work for the first version of the pipeline. It
refines [milestones](milestones.md) and [scraping plan](scraping-plan.md);
where they disagree, this document wins until they are edited. Facts
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
| Event-site link scan | discovery nicety; overrides cover the 14 known WDR events | `site` watch kind reserved |
| Heats, judge linking, generic long-tail adapters, LLM draft tool (M5) | data is thin or manual | `heats` and `judges` tables published, `judges` filled, `heats` empty |
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
the Mac's home directory is visible inside the machine (**unverified**:
OrbStack documents the Mac filesystem at `/mnt/mac`, and the home
directory at the same path as on the Mac; confirm in WP0); it has
network access; `orb` gives a shell and `journalctl` in one command.

```
orb create --cpus 4 --memory 8G nixos:25.11 swingset
orb -m swingset                                # shell inside
sudo nixos-rebuild switch --flake /Users/skeswa/repos/skeswa/swingset#orb --impure
```

`--impure` is needed because `nix/hosts/orb.nix` imports the
machine-generated `/etc/nixos/orbstack.nix`. If that import does not
work (**unverified**), the fallback is to edit the machine's
`/etc/nixos/configuration.nix` to import `nix/module.nix` from the
repo path and set `services.swingset.enable = true`.

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
| Native wheels on NixOS | manylinux wheels (`pyarrow`, `duckdb`, `scipy`, `numpy`, `rapidfuzz`, `selectolax`) need `libstdc++` and `zlib` from the system. The devshell and the unit set `LD_LIBRARY_PATH` to `${stdenv.cc.cc.lib}/lib:${zlib}/lib`. **Unverified** until WP0's import smoke test. Fallback, in order: take those packages from nixpkgs and give uv a venv with `--system-site-packages`; then, if still broken, drop uv and use `python312.withPackages` for everything |
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

## 3. Repository layout for v1

The layout in [repository layout](repository-layout.md) stands. v1
creates these files; `dcn/`, `generic/`, and `wayback.py` wait.

```
flake.nix  flake.lock  pyproject.toml  uv.lock  uv.toml
nix/module.nix                 services.swingset.* options, user, state dir, units, timers
nix/hosts/orb.nix              OrbStack machine: imports /etc/nixos/orbstack.nix, enables the service
config/hosts.toml              defaults from fetching.md plus playbook section 6 values
config/sources.toml            enabled flags, index URLs, index intervals
overrides/{event_aliases,source_urls,identity_overrides,suppressions,nicknames}.csv
docs/enums.md  docs/runbook.md
src/swingset/
  cli.py  config.py  clock.py  log.py
  state/{db,stages,findings}.py  state/migrations/0001_init.sql ...
  model/{schema,enums,ids,observations,canonical}.py
  fetch/{client,classify,politeness,robots,archive}.py
  schedule/{watches,policy,discover,cycle}.py
  sources/base.py              Source and PageKind protocols
  sources/wsdc_calendar/  sources/wsdc_registry/  sources/eepro/  sources/scoringdance/  sources/wdr/
  project/{writer,events,registry,contests}.py   observations -> canonical rows
  normalize/{names,divisions,events}.py
  link/{candidates,score,assign,confirm,overrides}.py  link/weights.toml
  build/{materialize,invariants,suppress,review,changelog,manifest}.py
  publish/{hub,card,candidate}.py  publish/card_template.md
  backup/{push,restore}.py
tests/                         mirrors src; fixtures live next to each source
.github/workflows/ci.yml
.github/ISSUE_TEMPLATE/{removal-request,site-operator}.md
```

## 4. Cross-cutting decisions

These are new. They are folded into the other design documents when v1
ships and indexed in the [decision log](decision-log.md).

1. **Watches own observations; canonical tables are projections.** A
   parser turns one snapshot into typed *observations* in the
   source's own vocabulary: an EEPro round page yields one
   `RoundSheet`, the calendar yields `CalendarRow`s, a registry
   lookup yields one `DancerLookup`, an index page yields
   `SourceEventRow`s. Observations are stored in the `observations`
   table keyed by the watch that produced them. A successful parse of
   a newer snapshot replaces that watch's whole observation set in
   one transaction. A parser writes nothing else.

   An observation's scope is a *source reference*, never a canonical
   id: `("source_event", "eepro:asc2025")`, `("source_event",
   "scoringdance:304")`, `("dancer", "123")`, `("source_index",
   "eepro")`, `("calendar", "wsdc")`. Which `event_id` a source event
   belongs to is decided by the *matching map* (`source_event_map`),
   itself a projection of index observations, calendar observations,
   `event_aliases.csv`, and `overrides/source_urls.csv`. The last is
   how a source without an index (World Dance Registry, the long
   tail) states its event: each row yields a stable source reference,
   `<source>:<platform key>` where the source extracts the key from
   the URL (`wdr:<uuid>`; `sha256(url)[:16]` for generic adapters),
   and maps it to the row's `event_id` with `match_method =
   override`, the highest precedence. The same row seeds the watch,
   so the watch's `source_ref` and the map entry always agree.
   Canonical ids such as `contest_id` are
   computed at projection time from the mapped `event_id`, so an
   alias change regroups already stored observations without
   re-parsing anything.

   Canonical rows (`events`, `contests`, `entries`, `dancers`, ...) are
   computed by pure *projection* functions from the current
   observations whose scopes resolve to one canonical scope (one
   event, one dancer, one source index) together with overrides and
   vocabularies. When any observation in a scope changes, or when the
   matching map moves a source reference between events, every
   affected canonical scope (both the old and the new event) is
   re-projected in one transaction:
   rows are upserted by primary key, keeping `first_seen_at`, and
   rows the scope no longer produces are deleted. An entry seen in
   prelims and in finals exists as long as any observation mentions
   it; `rounds_danced`, `best_round`, `entry_count`, and
   `promoted_count` fall out of the union. Judges span contests the
   same way. No table needs an owner column.

   Precedence when two observations state the same fact differently:
   the more specific page kind wins (round over event over index);
   among equals the later `fetched_at` wins; every disagreement is a
   `conflict` finding (decision 5) naming both snapshots. Provenance
   columns on a canonical row name the snapshot whose observation
   won. Re-parsing, re-projecting, and restoring are the same
   operation: recompute from what is stored.

2. **Stages run on input fingerprints, not on "something was
   fetched".** Each stage after fetch declares its inputs. `parse`:
   pending snapshot ids and every `EXTRACT_VERSION` and
   `PARSER_VERSION`. `project`: the observation set version, the
   vocabulary files, `event_aliases.csv`, `source_urls.csv`. `link`: the canonical set
   version, the `dancers` version, `weights.toml`, `nicknames.csv`,
   `identity_overrides.csv`, `LINKER_VERSION`. `build`: the link
   version, `suppressions.csv`, `schema_version`, the package
   version. `publish`: the candidate's manifest hash against the
   baseline's. `stage_state` holds the fingerprint each stage last
   completed on; a stage runs when the current fingerprint differs.
   Set versions are `revisions` counters bumped in the same
   transaction as the write they describe (`observations`,
   `source_event_map`, `canonical`, `dancers`, `links`, `findings`,
   `snapshots`), so nothing has to be hashed. `build` additionally
   depends on `findings` and `snapshots`, because `review_queue` and
   the published `snapshots` table are built from them: a cross-check
   that opens findings, or a fetch whose extract failed, changes the
   dataset without changing a canonical row.
   This replaces the rule in [operations](operations.md#timers) that
   skips steps 3 to 6 when nothing was fetched; that document is
   edited in WP10. An override committed between two unchanged polls
   reaches the Hub on the next cycle.

3. **Publish goes through an immutable candidate; the baseline moves
   only after success; at most one candidate is ever pending.**
   `build` writes `candidates/<run_id>/` with every table, the
   manifest, the card, the changelog delta against `baseline/`, and a
   `BUILT` marker naming the baseline commit and the build input
   fingerprint, and never touches it again. `publish` writes a
   `PUBLISHING` marker, creates the commit with `parent_commit` set
   to `baseline/COMMIT` and a message carrying the `run_id` and the
   manifest hash, writes `PUBLISHED` holding the new commit SHA, then
   promotes by atomically renaming the `baseline` symlink onto the
   candidate. A candidate is *pending* from the moment `PUBLISHING`
   is written until the `baseline` symlink points at it; `PUBLISHED`
   only records that the remote side is done. There can be only one
   pending candidate, because publish refuses to start while one
   exists. The published `changelog` table is the
   baseline's changelog plus the delta and lives inside the
   candidate, so history advances only on promotion. A dry run
   builds a candidate with `BUILT` only, which is disposable. Old
   candidates are pruned to the last five, never the baseline or a
   pending one.

   Cycle order is therefore: fetch, parse, project, link,
   **reconcile**, build, publish. Reconcile runs before build, takes
   the execution mode, and looks at the pending candidate, if any.
   With `PUBLISHED` present: the remote is done; finish the promotion
   locally without any network call, in every mode. Without it, ask
   the Hub for the head. Head message names the pending `run_id`:
   write `PUBLISHED`, promote. Head equals `baseline/COMMIT`: the
   commit never landed; in a real run publish the same candidate
   again now, then continue; in a dry run, report the pending
   candidate in the log, the run summary, and `doctor`, skip build
   and publish, and leave everything as it is, because a dry run
   never creates a commit. Head is anything else: someone else
   committed; fail the run and touch nothing.
   Only after reconcile does build ask whether its inputs changed,
   and a new candidate is always built against the promoted
   baseline, so its delta is right even when observations arrived
   while the acknowledgment was lost. `build` is idempotent by
   fingerprint: if a `BUILT` candidate for the current fingerprint
   already exists it is reused, and deleting a candidate deletes its
   `stage_state` row, so nothing can be left "complete" without a
   directory behind it. `last_published/` in [build](build.md) and
   [operations](operations.md) becomes `baseline/`.

4. **Responses are classified before any host state changes.**
   `classify(response, page_kind, watch) -> Classification`, one of
   `Ok`, `NotModified`, `ExpectedUnavailable`, `Gone`,
   `Throttled(retry_after)`, `Blocked`, `ServerError`, `Redirect`,
   `Invalid`. Host state (`paused_until`, pause streak, run failure)
   changes only on `Throttled`, `Blocked`, and `ServerError`. A page
   kind declares which statuses it expects and when: WDR's
   `routeInfo.json` declares 403 as `ExpectedUnavailable` while the
   watch has never had a 200, and `Blocked` after it has, which is
   the playbook's "403 on a known-good URL" rule. EEPro and
   scoring.dance declare none, so their 403 is `Blocked`. Challenge
   detection (`cf-chl`, `Just a moment`, a Cloudflare challenge body
   on any status) runs on every body and is always `Blocked`.
   `ExpectedUnavailable` and `Gone` change only the watch.

5. **Findings are evidence; the review queue is a view.** Anything a
   human should look at that cannot be recomputed from state is
   stored in `findings` with structured evidence: parser warnings
   (keyed by watch and code, replaced together with that watch's
   observations), observation conflicts, registry cross-check
   discrepancies, invalid registry responses, unknown enum values.
   Items that can be recomputed (ambiguous links, unsupported
   contests, unmatched source events) are not stored; `build`
   computes them. `review_queue` is open findings plus computed
   items. A finding closes when an override resolves it or when the
   watch that raised it is re-parsed without it. Findings are backed
   up with the state. The same rule covers one-off inputs: the
   registry dump used for the cross-check is archived as a blob, so
   the check can be re-run.

6. **A cycle can be killed anywhere and rerun; the result is the
   same as an uninterrupted run.** Rules that make this true:
   - **Every unit of work is one SQLite transaction** and every
     stage's "what is pending" is a predicate over state, never a
     list in memory. Fetch: watches with `next_check_at <= now`.
     Parse: snapshots with `parse_status != ok` or `parser_version`
     below the page kind's current version. Project and link: rows in
     `dirty_scopes`, written in the same transaction as the
     observation, map, or link change that dirtied them and deleted
     in the same transaction as the scope's re-projection or re-link.
     Build and publish: the candidate markers of decision 3.
     `stage_state` is written only after a stage drains its predicate.
   - **Two stores, one order.** A blob is written to `blobs/` (and an
     extract to `extracts/`) before the SQLite transaction that
     references it. A crash in between leaves an orphan, which is
     content-addressed and harmless; `swingset gc` removes orphans
     older than a day and is never run by a timer in v1.
   - **Graceful stop.** On SIGTERM the cycle stops issuing requests,
     lets the in-flight request finish for up to the request timeout
     or abandons it (nothing was written either way), commits the
     current unit, writes `runs/<run_id>.json` with `stopped = true`,
     releases the lock, and exits 0. The unit sets
     `KillMode=mixed` and `TimeoutStopSec=45`. A wall-clock budget
     applies to every stage, not only poll; a stage that runs out
     stops at a unit boundary and the next cycle continues.
   - **Operator pause.** `swingset pause --all | --host <h> | --source
     <s> [--until <time>]` and `swingset resume` write `paused_until`
     rows (`hosts` for a host, `cursors` for a source or the whole
     pipeline). A paused cycle runs no fetch, still runs the stages
     after fetch (so a committed override is published while paused),
     and reports the pause in the summary and `doctor` without
     counting it as an error. `systemctl stop swingset-cycle.timer`
     is the other switch and loses nothing.
   - **Resume has no catch-up.** After any pause every overdue watch
     is checked once at its normal priority, bounded by the gates and
     the daily budgets, and then follows its schedule. Missed polls
     are not replayed. Live intervals that were doubling reset on
     the first change as usual.
   - **Concurrency.** One flock in the state directory is shared by
     cycle, backup, and every manual command that writes state. The
     kernel releases it on death. A command that finds it held exits
     0 with one log line, except `doctor`, which is read-only.
   - **The test that proves it.** A crash-injection harness runs a
     cycle against fixtures with a hook that raises at the N-th
     transaction boundary, for every N, then reruns the cycle to
     completion and asserts the state (rows, blobs, candidates,
     `stage_state`) equals an uninterrupted run's. The same harness
     sends SIGTERM at each boundary. It is a done criterion for WP3
     and WP5 and runs in CI.
7. **Publish has a dry run.** `swingset publish --dry-run` builds the
   candidate and stops before the commit. `swingset cycle --dry-run`
   passes it through. The machine runs dry until the owner sets
   `HF_TOKEN`; nothing else changes.
8. **Every table is published from the first publish**, empty where
   v1 has no data (`heats`), so the schema, configs YAML, and
   consumer examples are complete from day one and never reshuffle.
9. **Fixtures are committed**, as [parsing](parsing.md#fixtures-and-tests)
   says. They are real bodies from the archive. A suppression request
   that names a person in a fixture is handled by re-recording the
   fixture from a different event; that case is listed in the runbook
   and is expected to be rare. (`research/verification/` kept headers
   only; that was a research choice, not the rule for fixtures.)
10. **Requests are described, not just URLs.** A watch has `method`,
    `url`, and `form` (JSON, nullable) so the registry's POST is a
    watch like any other. Snapshots record the same three fields.
11. **Hand-set link weights** in `link/weights.toml`, versioned, with
    a `LINKER_VERSION` stored on `identity_links` rows. Splink fitting
    is an offline notebook in v1.1 that proposes a new weights file.
12. **Overrides are read from the repo checkout on every cycle**,
    never copied into SQLite, so a `git pull` is the whole deploy
    step for a correction. Their hashes are stage inputs (decision 2),
    so a changed override is applied on the next cycle even when no
    poll changed. The module keeps the checkout path in
    `services.swingset.overridesDir`.
13. **The scheduler picks work by priority, then by `next_check_at`.**
    Priority order: `live`, `cooling`, index, `upcoming`, `archived`,
    registry, `backfill`. The registry sweep sits below `archived` so
    a busy weekend is never slowed by it.

## 5. SQLite schema

One file, WAL mode, `foreign_keys = ON`, schema versioned by numbered
SQL migrations under `state/migrations/`. Names match the published
tables where a table is published.

Internal tables:

| Table | Key columns | Purpose |
|---|---|---|
| `meta` | `key` | `schema_version`, `installed_at` |
| `runs` | `run_id` | `started_at`, `finished_at`, `dry_run`, `summary_json` |
| `hosts` | `host` | `next_allowed_at`, `paused_until`, `pause_reason`, `pause_streak`, `robots_sha256`, `robots_fetched_at`, `robots_status` |
| `host_budget` | `host`, `day` | `requests`, `bytes` |
| `cursors` | `name` | `value`; `registry_sweep_next`, `registry_probe_max_id`, `registry_probe_misses`, `paused_until:all`, `paused_until:source:<s>` |
| `watches` | `watch_id` | every column in [scheduling](scheduling.md#watches) plus `method`, `form`, `fingerprint`, `extract_version`, `priority`, `created_by_snapshot_id`, `parent_watch_id`, `ever_ok` (for decision 4) |
| `snapshots` | `snapshot_id` | every column in [fetching](fetching.md#archive) plus `method`, `form`, `via`, `headers_json`, `classification`, `extract_status`, `extract_sha256`, `parse_status`, `parsed_at`, `parser_version` |
| `observations` | `observation_id` | `watch_id`, `snapshot_id`, `kind`, `scope_kind` (`source_event`, `dancer`, `source_index`, `calendar`), `scope_id` (a source reference such as `eepro:asc2025`, never a canonical id), `seq`, `parser_version`, `payload_json`; indexed by `watch_id` and by (`scope_kind`, `scope_id`) |
| `source_event_map` | `source`, `source_ref` | `event_id`, `match_method` (`name_date`, `alias`, `override`), `match_confidence`; the projection that resolves observation scopes to events; rewritten whenever index or calendar observations, `event_aliases.csv`, or `source_urls.csv` change |
| `revisions` | `name` | monotonically increasing counter per set (`observations`, `source_event_map`, `canonical`, `dancers`, `links`, `findings`, `snapshots`), bumped in the writing transaction |
| `dirty_scopes` | `stage`, `scope_kind`, `scope_id` | `dirtied_at`, `run_id`; the durable work list for `project` and `link` (decision 6) |
| `stage_state` | `stage` | `input_fingerprint`, `completed_at`, `run_id`, `candidate_run_id` (build only) |
| `findings` | `finding_id` | `kind`, `subject_kind`, `subject_id`, `watch_id`, `snapshot_id`, `severity`, `summary`, `evidence_json`, `suggested_override`, `opened_at`, `run_id`, `closed_at`, `closed_by` |
| `source_events` | `source`, `source_ref` | projection of index observations: `name_raw`, `start_date`, `end_date`, `location_raw`, `url`, plus provenance; joins to `source_event_map` for the `event_id` |
| `backup_uploads` | `path` | `sha256`, `uploaded_at`; which blobs the archive repo already has |

Canonical tables, one per published table in [data model](data-model.md#tables),
with the published columns; the `snapshot_id` provenance column names
the winning observation's snapshot. `review_queue` is not stored
(decision 5). `changelog` is not stored in SQLite; it lives in the
candidate and baseline directories (decision 3).

`watch_id` is `sha256(source|kind|method|url|form)[:16]`, so discovery
is idempotent by construction. `observation_id` is
`sha256(watch_id|snapshot_id|kind|seq)[:16]`.

State directory layout: `state.sqlite`, `blobs/`, `extracts/`,
`candidates/<run_id>/{data,README.md,_meta,BUILT,PUBLISHING,PUBLISHED}`,
`baseline -> candidates/<run_id>`, `runs/`, `venv/`, `uv-cache/`.

## 6. Interfaces

Written down so work packages can be built and tested apart.

```python
# model/observations.py: source-vocabulary output of parsers
class Observation(Protocol):
    kind: str                                   # "eepro.round_sheet", "wsdc_calendar.row"
    def scope(self) -> tuple[str, str]: ...     # ("source_event", "eepro:asc2025") | ("dancer", "123") | ("source_index", "eepro") | ("calendar", "wsdc")

@dataclass(frozen=True)
class Warning: code: str; message: str; evidence: dict[str, Any]

# sources/base.py
class PageKind(Protocol):
    kind: str
    EXTRACT_VERSION: int
    PARSER_VERSION: int
    change_mode: Literal["validators", "body_hash", "extract"]
    def expected_statuses(self, watch: Watch) -> frozenset[int]: ...   # decision 4; usually empty
    def extract(self, body: bytes) -> Extract: ...                     # raises ExtractError
    def parse(self, ex: Extract, ctx: ParseContext) -> ParseResult: ...

@dataclass(frozen=True)
class ParseContext: snapshot_id: str; watch_id: str; url: str; source: str; kind: str; source_ref: str | None; fetched_at: datetime
# no event_id: a parser never sees canonical ids

@dataclass
class ParseResult: observations: list[Observation]; watches: list[WatchSpec]; warnings: list[Warning]

class Source(Protocol):
    name: str
    hosts: tuple[str, ...]
    page_kinds: Mapping[str, PageKind]
    def seed_watches(self, cfg: SourceConfig, overrides: Overrides) -> list[WatchSpec]: ...
    def policy(self, watch: Watch, event: Event | None, host: HostConfig, now: datetime) -> Policy: ...

# project/: pure, deterministic
def project_map(index_obs: Sequence[Observation], calendar_obs: Sequence[Observation], aliases: Aliases, source_urls: SourceUrls) -> SourceEventMap
def project(scope: CanonicalScope, observations: Sequence[Observation], ctx: ProjectContext) -> Projection
# CanonicalScope = ("event", event_id) | ("dancer", wsdc_id) | ("source_index", source) ; the writer selects the
# observations whose source scopes resolve to it through the current SourceEventMap
# Projection = canonical rows by table + conflicts (findings) ; ProjectContext = overrides, vocabularies, existing dancers view

# publish/candidate.py
def reconcile(state_dir: Path, hub: Hub, mode: Literal["real", "dry_run"]) -> Reconciled | PendingReported | NothingPending   # before build; decision 4.3
def build_candidate(fingerprint: str, ...) -> Candidate                       # reuses a BUILT candidate for the same fingerprint

# fetch/classify.py
def classify(resp: Response | Exception, kind: PageKind, watch: Watch) -> Classification

# fetch/politeness.py
class Gate:
    def acquire(self, host: str, now: datetime) -> Grant | Wait | Paused: ...
    def release(self, host: str, cls: Classification, now: datetime) -> None: ...   # host state changes only here

# state/stages.py
def inputs(stage: Stage, db: DB, files: OverrideFiles) -> str        # fingerprint
def due(stage: Stage, db: DB, files: OverrideFiles) -> bool
def complete(stage: Stage, fingerprint: str, run_id: str) -> None
```

Canonical rows are frozen dataclasses in `model/canonical.py`, one per
published table, with a `key()` method. Parsers never construct them;
only `project/` does, and `link/` fills the link columns afterward
through the same writer.

## 7. Work packages

Sizes: S is a session, M is two or three, L is a weekend or more. Each
package ends with tests green and a commit. Human tasks that must
happen alongside are in section 8.

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
transactions); `migrations/0001_init.sql` with every table in section
5; `model/ids.py` implementing every id in
[data model](data-model.md#identifiers) with tests for slugging,
`-2` suffixes, and the name-form entry id; `model/enums.py` as
`StrEnum`s, and `docs/enums.md` generated from them by
`swingset enums --write`; `model/observations.py` and
`model/canonical.py` (the two sides of decision 4.1);
`state/stages.py` and `state/findings.py`.

Done when: a fresh database migrates, `swingset doctor` shows schema
version 1, and id tests cover every example in the data model.

### WP2. Fetch core (M)

Deliverables: `fetch/client.py`, `fetch/politeness.py`,
`fetch/robots.py`, `fetch/archive.py` as specified in
[scraping plan](scraping-plan.md#phase-0-fetch-core-milestone-m0)
and [fetching](fetching.md): one in flight per host, `next_allowed_at`,
`Crawl-delay`, `Retry-After` (seconds and date), request and byte
budgets per day, `fetch/classify.py` as in decision 4.4 with the
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
priority order in 4.13; `schedule/cycle.py` running the steps of decision 4.3 with a
wall-clock budget checked at every unit boundary in every stage,
SIGTERM handling as in decision 4.6, `runs/<run_id>.json`, and each
post-fetch stage gated by `state/stages.py` (decision 4.2) and
drained from its pending predicate; `swingset pause` and `swingset
resume`; the observation store, `dirty_scopes`, and
`project/writer.py` (transactional scope re-projection driven by
`dirty_scopes`, upsert by key keeping `first_seen_at`, delete of rows
no longer produced, conflict findings); the first projections: `project/events.py`
(calendar observations to `events`), index observations to
`source_events`, and `project_map` producing `source_event_map`,
with the writer re-projecting every event whose membership changed
when the map changes; `schedule/discover.py` steps 1, 3, 4, and 5 of
[scheduling](scheduling.md#discovery) (calendar, matching, event
watches, `source_urls.csv`), where matching is the map projection,
ambiguous matches are computed review items, and
`overrides/event_aliases.csv` (`source, source_ref, event_id, note`)
and `overrides/source_urls.csv` are map inputs. The cycle runner
also carries the reconcile step of decision 4.3 as a no-op until WP5
fills it in.

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
decision 4.6 passes for fetch, parse, and project at every
transaction boundary and on SIGTERM; a paused cycle
(`swingset pause --all`) makes no request but still projects a
changed alias; `swingset resume` after a simulated week checks each
overdue watch once and then returns to schedule.

### WP4. NixOS module and the machine (S). Ends M0 with WP5's restore.

Deliverables: `nix/module.nix` with options `enable`, `package`,
`stateDir` (default `/var/lib/swingset`), `environmentFile`,
`overridesDir`, `cycleBudget` (default `12m`), `dryRun` (default
`true`); a `swingset` user and group; `swingset-cycle.service` and
`.timer` (every 15 min, `RandomizedDelaySec=120`, `Persistent=true`),
`swingset-backup.timer` (Mon to Thu 04:00; Fri to Sun 04:00, 12:00,
20:00), `swingset-summary.timer` (08:00); a flock in the state
directory shared by cycle and backup; `ExecStartPre` running
`uv sync --frozen --no-dev` into the state directory; hardening
(`DynamicUser=false`, `ProtectSystem=strict`, `ReadWritePaths`
limited to the state directory, `PrivateTmp`, no new privileges),
`KillMode=mixed` and `TimeoutStopSec=45` so a stop during a cycle is
the graceful stop of decision 4.6.
`nix/hosts/orb.nix` and `nixosConfigurations.orb` in the flake.
`docs/runbook.md` sections: create the machine, rebuild, read logs,
set the token, rotate the token, pause and resume (the command, the
timer, and what each loses: nothing), disable a source, stop the
machine safely mid-cycle, restore.

Done when: on the machine, `systemctl list-timers` shows the three
timers, four consecutive cycles run against the calendar only,
`journalctl` shows each cycle's one-line summary, and
`systemctl stop swingset-cycle` during a cycle exits 0 within
45 s with a `stopped = true` run file and a clean next cycle.

### WP5. Build, publish, backup, restore (M). Completes M0.

Deliverables: `model/schema.py` with a PyArrow schema per table from
[data model](data-model.md#tables); `build/materialize.py` writing
every table sorted by key with content-defined chunking and page
index, year partitions for `callback_marks` and `final_marks`;
`build/invariants.py` with the five checks in [build](build.md);
`build/suppress.py` from `overrides/suppressions.csv`
(`wsdc_id, name_raw, event_id, reason, date`); `build/review.py`
composing `review_queue` from open findings and computed items;
`build/changelog.py` computing the delta against `baseline/` by key
with DuckDB; `build/manifest.py`; `publish/candidate.py` implementing
decision 4.3 (candidate directory, `COMMIT` and `PUBLISHED` markers,
atomic symlink promotion, pruning, recovery). `publish/hub.py` using
`create_commit` with `parent_commit = baseline/COMMIT`, the `run_id`
and manifest hash in the message, one commit per publish, only when
the candidate differs from the baseline; `publish/card.py` filling
`card_template.md` with the configs YAML, row counts, coverage, and
the consumer examples from
[publishing](publishing.md#consumer-examples-go-in-the-card).
`backup/push.py` (SQLite backup API copy, new blobs from
`backup_uploads`, the current baseline candidate, `runs/`, one
commit, nothing if unchanged); `backup/restore.py`
(`snapshot_download` into an empty state directory, recreate the
`baseline` symlink, verify `PRAGMA integrity_check`).

Tests: build twice from the same state gives byte-identical
candidates; each invariant has a failing fixture; suppression nulls
exactly the listed columns; changelog detects add, remove, update;
the published changelog equals baseline plus delta and a dry run
leaves the baseline untouched; a failure injected after
`create_commit` returns and before `PUBLISHED` is reconciled on the
next run by reading the Hub head (`respx`), both when no inputs
changed (promote, then nothing) and when new observations arrived in
between (promote first, then build a second candidate whose delta is
against the promoted one, then publish it); a failure injected after
`PUBLISHING` and before `create_commit` is retried with the same
candidate on the next run, not rebuilt; a failure injected after
`PUBLISHED` is written and before the symlink rename is finished on
the next run by promotion alone, with `respx` asserting no request
was made; a `cycle --dry-run` with a `PUBLISHING` candidate and an
unadvanced head makes no request, promotes nothing, skips build, and
reports the pending candidate in the run summary; a third party's commit at
the head fails the run without promoting or building; a `BUILT`-only
candidate from a dry run does not block a later build and is reused
when the fingerprint is unchanged; deleting a candidate directory
makes build due again; an override-only cycle (no fetch,
`suppressions.csv` changed) runs build and publish and produces
exactly one commit; a cycle with nothing fetched and no input changed
runs no stage and makes no network call; the crash-injection harness
of decision 4.6 passes across link, reconcile, build, and publish at
every transaction boundary and marker write, with `respx` counting
exactly one `create_commit` in every recovery path; `swingset gc`
removes only orphan blobs older than a day.

Done when: the owner creates the two Hub repos, sets `HF_TOKEN` in the
machine's environment file, flips `dryRun = false`, and the next cycle
publishes `events` (plus every empty table) with the card; then a
fresh `swingset-restore` machine runs `swingset restore` and its
`doctor` matches the first machine. That is M0.

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
rate above 10 percent in a run fails the run. The miss shape is
**unverified** today, so the first task of this package is a manual
`swingset fetch-one` on a few ids expected to be absent (well above
the current maximum); the response becomes the `NotFound` fixture and
the rule is written from it. Until that fixture exists, `NotFound`
matches nothing and every miss is `Invalid`, which is safe: the sweep
cannot skip a valid dancer and the probe cannot terminate early.

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
the event-scope projection of decision 4.1 that turns round, event,
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
determinism (same input, same links); a changed `weights.toml` alone
makes `link` due.

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
requests, made once, after the operator conversation in section 8).

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
`gone`) instead of pausing the host (decision 4.4). Discovery from
`overrides/source_urls.csv` (`event_id, source, kind, url, parser,
notes`), seeded by a one-off script from `research/results-sources.csv`
(14 rows; the script lives in `research/` and is run once).

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
  pending candidate if any, dirty scopes, review queue size, last
  publish SHA.
- Parse failure rate above 10 percent for a source fails the run;
  a paused host fails the run and leads the summary.
- `swingset reparse --kind <kind>` and `--since` after a version bump.
- `docs/runbook.md` complete; `README.md` rewritten as the page the
  User-Agent points at: what we collect, why, how to opt out, how to
  ask us to slow down, the issue templates.
- The dataset card's coverage table and gaps list (the four
  `not_found` events and the Facebook-only ones).
- Fold section 4 into the design documents and the decision log:
  [architecture](architecture.md) gains the observation and
  projection stages; [operations](operations.md#timers) drops the
  "skip 3 to 6" rule for stage fingerprints; [build](build.md) and
  [publishing](publishing.md) replace `last_published/` with the
  candidate and baseline protocol; [fetching](fetching.md) gains
  response classification; [data model](data-model.md) notes that
  `review_queue` is a view over findings. Move answered open
  questions into the playbooks.

## 8. Human tasks alongside the code

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

## 9. Order and rough calendar

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

## 10. Definition of done for v1

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
- `swingset restore` on a fresh machine reproduces `doctor` output.
- `pytest` passes offline, including the override-only cycle, the
  crash-injection harness at every boundary, and SIGTERM at every
  boundary; CI is green; `mypy --strict` is clean.
- The machine has been stopped mid-cycle and resumed at least once
  during a live weekend with no duplicate rows, no lost snapshot, and
  no extra Hub commit.
- Every load number in `runs/*.json` for a live weekend is at or under
  the playbook's estimate for that host.

## 11. Things to verify during v1

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

## 12. Risks

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
