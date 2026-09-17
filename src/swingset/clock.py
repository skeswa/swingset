"""Injectable UTC clock; all scheduling and waits use this boundary."""

import time
from datetime import UTC, datetime, timedelta
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...
    def sleep(self, seconds: float) -> None: ...
    def monotonic(self) -> float: ...


class SystemClock:
    def monotonic(self) -> float:
        return time.monotonic()

    def now(self) -> datetime:
        return datetime.now(UTC)

    def sleep(self, seconds: float) -> None:
        time.sleep(max(0, seconds))


class FakeClock:
    def __init__(self, now: datetime | None = None) -> None:
        self.current = now or datetime(2026, 1, 1, tzinfo=UTC)
        if self.current.tzinfo is None:
            raise ValueError("clock requires timezone-aware UTC time")
        self.sleeps: list[float] = []
        self.elapsed = 0.0

    def monotonic(self) -> float:
        return self.elapsed

    def now(self) -> datetime:
        return self.current

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.elapsed += max(0, seconds)
        self.current += timedelta(seconds=max(0, seconds))
