"""Validated, immutable operating policy loaded once per command."""

import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, fields
from datetime import date
from pathlib import Path
from types import MappingProxyType

from swingset.model import history


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
class Config:
    hosts: Mapping[str, HostConfig]
    sources: Mapping[str, SourceConfig]
    history_start: date = history.HISTORY_START

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
    )
