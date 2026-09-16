"""The history start date: the earliest point from which swingset traces events.

`docs/reference/backfill.md#the-start-date-rule` owns the contract. Events that
ended before the start date get no rows; the registry mirror stays whole
and earlier registry placements keep a null event id. The default below
is the project's enshrined start. `config/sources.toml` may restate it
under the top-level `history_start` key.
"""

from __future__ import annotations

from datetime import date, datetime

HISTORY_START = date(2010, 1, 1)


def parse_history_start(value: object) -> date:
    """Accept a TOML local date or an ISO `yyyy-mm-dd` string; reject anything else."""
    if isinstance(value, datetime):
        raise ValueError("history_start must be a date without a time")
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError as error:
            raise ValueError(f"invalid history_start: {value!r}") from error
    raise ValueError(f"invalid history_start: {value!r}")


def _as_date(value: date | str) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def in_history(
    history_start: date,
    *,
    end_date: date | str | None = None,
    start_date: date | str | None = None,
    year: int | None = None,
) -> bool:
    """Apply the start-date rule to one event.

    An event is in scope when the day it ended is on or after the history
    start. Without an end date the start date decides, then the year alone.
    An event with no date at all stays in scope; the rule never drops rows
    it cannot judge.
    """
    anchor = end_date if end_date is not None else start_date
    if anchor is not None:
        return _as_date(anchor) >= history_start
    if year is not None:
        return int(year) >= history_start.year
    return True
