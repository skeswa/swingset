"""Measured starting allocations; limits remain owned by the existing host gate."""

from __future__ import annotations

import math
import tomllib
from dataclasses import dataclass, fields
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from swingset.config import Config


@dataclass(frozen=True)
class SchedulerConfig:
    reconciliation_share: float = 1 / 24
    acquisition_share: float = 5 / 12
    offline_share: float = 13 / 24
    service_gap_seconds: float = 86400
    pending_parse_bytes: int = 128 * 1024 * 1024
    pending_parse_items: int = 1000
    pending_work_items: int = 10000
    repair_requests_per_cycle: int = 2
    metadata_recovery_attempts: int = 3
    metadata_recheck_seconds: float = 2592000
    unavailable_recheck_seconds: float = 7776000
    event_turn_requests: int = 4
    listed_page_percent: int = 50
    event_pressure_high: int = 100
    event_pressure_low: int = 80
    event_pressure_max_age_seconds: float = 86400
    event_pressure_refresh_events: int = 8
    event_pressure_probe_pages: int = 32

    def __post_init__(self) -> None:
        for field in fields(self):
            value = getattr(self, field.name)
            if (
                isinstance(value, bool)
                or not isinstance(value, int | float)
                or not math.isfinite(value)
                or value <= 0
            ):
                raise ValueError(f"scheduling {field.name} must be positive and finite")
            if field.type == "int" and not isinstance(value, int):
                raise ValueError(f"scheduling {field.name} must be an integer")
        if self.event_turn_requests > 64:
            raise ValueError("event_turn_requests must be between 1 and 64")
        if self.listed_page_percent >= 100:
            raise ValueError("listed_page_percent must be between 1 and 99")
        if self.event_pressure_low >= self.event_pressure_high:
            raise ValueError("event_pressure_low must be below event_pressure_high")
        if self.event_pressure_refresh_events > 100 or self.event_pressure_probe_pages > 32:
            raise ValueError("event pressure refresh must be at most 100 and page bound at most 32")
        if not math.isclose(
            self.reconciliation_share + self.acquisition_share + self.offline_share, 1
        ):
            raise ValueError("cycle scheduling shares must sum to one")


def parse_scheduler(body: bytes) -> SchedulerConfig:
    values = tomllib.loads(body.decode()).get("scheduling", {})
    if set(values) - {field.name for field in fields(SchedulerConfig)}:
        raise ValueError("unknown scheduling settings")
    return SchedulerConfig(**values)


def shares(host: str) -> dict[str, int]:
    # Initial objectives in journal/investigations/2026/h14-shadow-load-2026-09-13.md. An empty
    # eligible class lends its reservation; weights never increase host limits.
    weights = {
        "scoring.dance": (50, 25, 10, 15),
        "points.worldsdc.com": (10, 0, 70, 20),
        "web.archive.org": (50, 10, 20, 20),
    }.get(host, (40, 30, 20, 10))
    return dict(zip(("new", "current", "identity", "old"), weights, strict=True))


@dataclass(frozen=True)
class CycleAllocation:
    reconciliation_seconds: float
    acquisition_seconds: float
    offline_seconds: float


def allocation(config: Config, budget: float) -> CycleAllocation:
    if not math.isfinite(budget) or budget < 0:
        raise ValueError("cycle budget must be nonnegative and finite")
    policy = config.scheduler
    return CycleAllocation(
        budget * policy.reconciliation_share,
        budget * policy.acquisition_share,
        budget * policy.offline_share,
    )
