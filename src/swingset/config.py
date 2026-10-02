"""Validated, immutable operating policy loaded once per command."""

import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, fields
from datetime import date
from pathlib import Path
from types import MappingProxyType

from swingset.model import history
from swingset.schedule.fair_policy import SchedulerConfig, parse_scheduler


def duration(value: str | int | float) -> float:
    if isinstance(value, int | float):
        seconds = float(value)
    else:
        match = re.fullmatch(r"(\d+(?:\.\d+)?)(s|m|h|d)?", value.strip())
        if not match:
            raise ValueError(f"invalid duration: {value!r}")
        seconds = float(match[1]) * {None: 1, "s": 1, "m": 60, "h": 3600, "d": 86400}[match[2]]
    if seconds < 0:
        raise ValueError("duration cannot be negative")
    return seconds


def byte_size(value: str | int) -> int:
    """Read a byte count written plainly or with a decimal KB/MB/GB suffix."""
    if isinstance(value, int):
        return value
    match = re.fullmatch(r"(\d+)(KB|MB|GB)?", value.strip())
    if not match:
        raise ValueError(f"invalid byte size: {value!r}")
    return int(match[1]) * {None: 1, "KB": 1000, "MB": 1000000, "GB": 1000000000}[match[2]]


@dataclass(frozen=True)
class HostConfig:
    min_gap_seconds: float = 5
    daily_request_budget: int = 200
    daily_byte_budget: int | None = None
    index_interval: float | None = None
    index_interval_weekend: float = 3600
    index_interval_weekday: float = 21600
    live_interval: float = 900
    cooling_interval: float = 21600
    round_live_interval: float = 43200
    round_cooling_interval: float = 86400
    challenge_pause: float = 86400
    sweep_daily_request_budget: int = 20000


@dataclass(frozen=True)
class SourceConfig:
    enabled: bool = False
    index_urls: tuple[str, ...] = ()


@dataclass(frozen=True)
class RetentionConfig:
    """How big the live database may get, and how much recent history stays local.

    `max_database_bytes` is measured against `state.sqlite` on disk, which is
    what backups and the disk actually carry; deleting rows frees pages to
    SQLite's free list without shrinking that file. `recent_window` is how many
    of the newest generations of each scope are retention roots. Both are
    reported by doctor. `collect_older_than` is the age a disposable candidate
    or artifact must reach before the collector may remove it, and it must be at
    least one second: that floor is all a build still writing its candidate has.

    The three `checkpoint_` values are the same kind of policy for the local
    checkpoints on the worker's disk, which the backup command prunes: how many
    of the newest timer checkpoints stay, how young any timer checkpoint may be
    and stay, and how long an abandoned temporary directory waits.
    """

    max_database_bytes: int = 48_000_000_000
    recent_window: int = 3
    collect_older_than: float = 86400
    checkpoint_keep_recent: int = 2
    checkpoint_max_age: float = 2 * 86400
    checkpoint_incomplete_max_age: float = 86400


@dataclass(frozen=True)
class StageConfig:
    """Which optional event bookkeeping stages a cycle runs.

    All default on, which is the behaviour before the switches existed. A stage
    that is off is skipped entirely. Turning `event_pressure` off also opens its
    acquisition gate: stale or unsynced pressure must not close `event_index`
    expansion when nothing refreshes it.
    """

    event_site_review: bool = True
    event_pressure: bool = True
    event_progress_refresh: bool = True
    event_blocker_observations: bool = True

    def disabled(self) -> list[str]:
        return sorted(f.name for f in fields(self) if not getattr(self, f.name))


@dataclass(frozen=True)
class DerivationConfig:
    """`[derivation]` in sources.toml. `cutoff = false` turns off output cutoff
    (D-0205): every re-run mints a new generation again, at schema 33."""

    cutoff: bool = True


#: `[release] path` values (plan phase 3, D-0224). `partitioned` assembles the
#: release from partition artifacts one partition at a time (`build/assembly.py`);
#: `whole` loads every artifact into memory and runs the builder unchanged;
#: `closure` is the phase 2 closure selection, kept for one release while phase 3
#: settles.
RELEASE_PATHS = ("partitioned", "whole", "closure")


@dataclass(frozen=True)
class ReleaseConfig:
    """`[release]` in sources.toml. Read from the accepted bundle at build
    time; not a derivation input, so a change re-queues nothing.

    ``streamed_lock`` keeps the heavy lock on a partitioned build too, so
    derive starts no unit beside it. It stays true until the worker's
    post-check shows `swingset-release` MemoryPeak under 2 GB with derive
    active (phase 3 rehearsal: 6.7 GB before F26, D-0224)."""

    path: str = "partitioned"
    streamed_lock: bool = True


@dataclass(frozen=True)
class StorageConfig:
    """`[storage]` in sources.toml: the maintain loop's storage tasks (phase 4,
    WP7; D-0229, D-0231, D-0232). Read from the accepted bundle by maintain;
    not a derivation input, so a change re-queues nothing.

    Each tick evicts at most `evict_refs_per_tick` references and packs at
    most `pack_rows_per_tick` bodies. The collector sweeps every
    `collect_every_days`, and the restore drill installs the newest backup set
    every `drill_every_days` into `<drill_dir>/swingset-drill-<set>`."""

    evict_refs_per_tick: int = 2_000_000
    pack_rows_per_tick: int = 20_000
    collect_every_days: int = 7
    drill_every_days: int = 7
    drill_dir: str = "/var/tmp"


@dataclass(frozen=True)
class Config:
    hosts: Mapping[str, HostConfig]
    sources: Mapping[str, SourceConfig]
    history_start: date = history.HISTORY_START
    scheduler: SchedulerConfig = SchedulerConfig()
    retention: RetentionConfig = RetentionConfig()
    stages: StageConfig = StageConfig()
    derivation: DerivationConfig = DerivationConfig()
    release: ReleaseConfig = ReleaseConfig()
    storage: StorageConfig = StorageConfig()

    def host(self, name: str) -> HostConfig:
        return self.hosts.get(name, HostConfig())

    def enabled(self, source: str) -> bool:
        return self.sources.get(source, SourceConfig()).enabled


def parse_hosts(body: bytes) -> Mapping[str, HostConfig]:
    raw = tomllib.loads(body.decode())
    result: dict[str, HostConfig] = {}
    allowed = {f.name for f in fields(HostConfig)}
    for name, values in raw.get("hosts", {}).items():
        if unknown := values.keys() - allowed:
            raise ValueError(f"unknown host settings for {name}: {sorted(unknown)}")
        parsed = dict(values)
        for key, value in values.items():
            if "interval" in key or key == "challenge_pause":
                parsed[key] = duration(value)
            elif key == "daily_byte_budget" and isinstance(value, str):
                match = re.fullmatch(r"(\d+)(KB|MB|GB)", value)
                if not match:
                    raise ValueError(f"invalid byte budget: {value}")
                parsed[key] = (
                    int(match[1]) * {"KB": 1000, "MB": 1000000, "GB": 1000000000}[match[2]]
                )
        host = HostConfig(**parsed)
        floor = 2 if name == "points.worldsdc.com" else 5
        if host.min_gap_seconds < floor or host.daily_request_budget < 1:
            raise ValueError(f"unsafe host limits for {name}")
        if host.daily_byte_budget is not None and host.daily_byte_budget < 1:
            raise ValueError(f"invalid byte budget for {name}")
        result[name] = host
    return MappingProxyType(result)


def parse_sources(body: bytes) -> Mapping[str, SourceConfig]:
    raw = tomllib.loads(body.decode())
    result = {}
    for name, values in raw.get("sources", {}).items():
        if values.keys() - {"enabled", "index_urls"}:
            raise ValueError(f"unknown source settings for {name}")
        enabled = values.get("enabled", False)
        urls = values.get("index_urls", [])
        if (
            not isinstance(enabled, bool)
            or not isinstance(urls, list)
            or not all(isinstance(u, str) for u in urls)
        ):
            raise ValueError(f"invalid source settings for {name}")
        result[name] = SourceConfig(enabled, tuple(urls))
    return MappingProxyType(result)


def parse_retention(body: bytes) -> RetentionConfig:
    """Read the `[retention]` table of sources.toml; absent means the defaults."""
    values = tomllib.loads(body.decode()).get("retention", {})
    if unknown := values.keys() - {field.name for field in fields(RetentionConfig)}:
        raise ValueError(f"unknown retention settings: {sorted(unknown)}")
    parsed = dict(values)
    if "max_database_bytes" in parsed:
        parsed["max_database_bytes"] = byte_size(parsed["max_database_bytes"])
    for name in ("collect_older_than", "checkpoint_max_age", "checkpoint_incomplete_max_age"):
        if name in parsed:
            parsed[name] = duration(parsed[name])
    result = RetentionConfig(**parsed)
    for name in ("recent_window", "checkpoint_keep_recent"):
        count = getattr(result, name)
        if not isinstance(count, int) or isinstance(count, bool):
            raise ValueError(f"retention {name} must be a whole number")
    if result.max_database_bytes < 1 or result.recent_window < 1:
        raise ValueError("retention limits must be positive")
    if result.checkpoint_keep_recent < 1:
        raise ValueError("retention checkpoint_keep_recent must keep at least one checkpoint")
    # The age floor is the only thing between a build that is still writing its
    # candidate directory and a removal, so zero is not a setting: it would let
    # an apply remove a directory the moment it appeared.
    if result.collect_older_than < 1:
        raise ValueError("retention collect_older_than must be at least one second")
    if result.checkpoint_max_age < 0 or result.checkpoint_incomplete_max_age < 0:
        raise ValueError("retention checkpoint ages cannot be negative")
    return result


def parse_stages(body: bytes) -> StageConfig:
    """Read the top-level `[stages]` table of sources.toml; absent means all on."""
    values = tomllib.loads(body.decode()).get("stages", {})
    if not isinstance(values, dict):
        raise ValueError("stages must be a table")
    if unknown := values.keys() - {field.name for field in fields(StageConfig)}:
        raise ValueError(f"unknown stages: {sorted(unknown)}")
    for name, value in values.items():
        if not isinstance(value, bool):
            raise ValueError(f"stage {name} must be true or false")
    return StageConfig(**values)


def parse_derivation(body: bytes) -> DerivationConfig:
    """Read the top-level `[derivation]` table of sources.toml; absent means cutoff on."""
    values = tomllib.loads(body.decode()).get("derivation", {})
    if not isinstance(values, dict):
        raise ValueError("derivation must be a table")
    if unknown := values.keys() - {field.name for field in fields(DerivationConfig)}:
        raise ValueError(f"unknown derivation settings: {sorted(unknown)}")
    for name, value in values.items():
        if not isinstance(value, bool):
            raise ValueError(f"derivation {name} must be true or false")
    return DerivationConfig(**values)


def parse_release(body: bytes) -> ReleaseConfig:
    """Read the top-level `[release]` table of sources.toml; absent means `partitioned`."""
    values = tomllib.loads(body.decode()).get("release", {})
    if not isinstance(values, dict):
        raise ValueError("release must be a table")
    if unknown := values.keys() - {field.name for field in fields(ReleaseConfig)}:
        raise ValueError(f"unknown release settings: {sorted(unknown)}")
    path = values.get("path", ReleaseConfig.path)
    if path not in RELEASE_PATHS:
        raise ValueError(f"release path must be one of {', '.join(RELEASE_PATHS)}")
    streamed_lock = values.get("streamed_lock", ReleaseConfig.streamed_lock)
    if not isinstance(streamed_lock, bool):
        raise ValueError("release streamed_lock must be true or false")
    return ReleaseConfig(path=path, streamed_lock=streamed_lock)


def parse_storage(body: bytes) -> StorageConfig:
    """Read the top-level `[storage]` table of sources.toml; absent means the defaults."""
    values = tomllib.loads(body.decode()).get("storage", {})
    if not isinstance(values, dict):
        raise ValueError("storage must be a table")
    if unknown := values.keys() - {field.name for field in fields(StorageConfig)}:
        raise ValueError(f"unknown storage settings: {sorted(unknown)}")
    result = StorageConfig(**values)
    for name in ("evict_refs_per_tick", "pack_rows_per_tick"):
        count = getattr(result, name)
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            raise ValueError(f"storage {name} must be a whole number, 0 or more")
    for name in ("collect_every_days", "drill_every_days"):
        count = getattr(result, name)
        if not isinstance(count, int) or isinstance(count, bool) or count < 1:
            raise ValueError(f"storage {name} must be a whole number of days, 1 or more")
    if not isinstance(result.drill_dir, str) or not result.drill_dir.startswith("/"):
        raise ValueError("storage drill_dir must be an absolute path")
    return result


def parse_history_start(body: bytes) -> date:
    """Read the top-level `history_start` of sources.toml; absent means the enshrined default."""
    raw = tomllib.loads(body.decode())
    if "history_start" not in raw:
        return history.HISTORY_START
    return history.parse_history_start(raw["history_start"])


def load_config(directory: Path = Path("config")) -> Config:
    sources = (directory / "sources.toml").read_bytes()
    return Config(
        parse_hosts((directory / "hosts.toml").read_bytes()),
        parse_sources(sources),
        parse_history_start(sources),
        parse_scheduler(sources),
        parse_retention(sources),
        parse_stages(sources),
        parse_derivation(sources),
        parse_release(sources),
        parse_storage(sources),
    )
