"""UTC-padded event windows and host-specific polling clocks."""

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from swingset.config import HostConfig


@dataclass(frozen=True)
class Policy:
    state: str
    interval: float | None
    priority: int


def event_state(start: date, end: date, now: datetime) -> str:
    start_at = datetime.combine(start, datetime.min.time(), UTC)
    end_at = datetime.combine(end, datetime.min.time(), UTC)
    if now < start_at - timedelta(days=14):
        return "dormant"
    if now < start_at - timedelta(hours=36):
        return "upcoming"
    if now <= end_at + timedelta(hours=48):
        return "live"
    if now <= end_at + timedelta(days=30):
        return "cooling"
    return "archived"


def policy(
    *,
    source: str,
    kind: str,
    host: HostConfig,
    now: datetime,
    start: date | None = None,
    end: date | None = None,
    state: str = "live",
    unchanged_streak: int = 0,
    jitter: float = 0,
) -> Policy:
    if state == "gone":
        return Policy("gone", None, 99)
    if source == "wsdc_registry":
        return Policy("registry", 86400 * 365, 5)
    if source == "wsdc_calendar":
        return Policy("live", host.index_interval or 86400, 2)
    if state == "backfill":
        return Policy("backfill", 90 * 86400, 6)
    if kind == "index" and start is None:
        weekend = now.weekday() >= 4 or (now.weekday() == 0 and now.hour < 12)
        return Policy(
            "live",
            host.index_interval
            or (host.index_interval_weekend if weekend else host.index_interval_weekday),
            2,
        )
    if start is not None and end is not None:
        state = event_state(start, end, now)
    if state == "dormant":
        return Policy(state, None, 99)
    if state == "upcoming":
        return Policy(state, 86400, 3)
    if state == "live":
        interval = (
            host.round_live_interval
            if kind == "round"
            else min(3600, host.live_interval * 2 ** min(unchanged_streak // 8, 2))
            + min(300, max(0, jitter))
        )
        return Policy(state, interval, 0)
    if state == "cooling":
        interval = (
            host.round_cooling_interval
            if kind == "round"
            else min(86400, host.cooling_interval * 2 ** min(unchanged_streak, 2))
        )
        return Policy(state, interval, 1)
    return Policy("archived", 90 * 86400, 4)
