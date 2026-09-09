"""Stable event-name normalization and calendar overlap matching."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date

from swingset.model.ids import series_slug


@dataclass(frozen=True)
class EventCandidate:
    event_id: str
    name: str
    start_date: date
    end_date: date


def normalize_event_name(value: str) -> str:
    """Normalize presentation differences while retaining edition words."""
    value = unicodedata.normalize("NFKD", value)
    value = "".join(char for char in value if not unicodedata.combining(char))
    value = re.sub(r"[^a-z0-9]+", " ", value.casefold())
    return " ".join(value.split())


def event_dates_overlap(
    left_start: date, left_end: date, right_start: date, right_end: date
) -> bool:
    return left_start <= right_end and right_start <= left_end


def matching_events(
    name: str, start_date: date, end_date: date, candidates: list[EventCandidate]
) -> tuple[EventCandidate, ...]:
    """Return deterministic exact-name/date matches for source discovery."""
    normalized = normalize_event_name(name)
    return tuple(
        sorted(
            (
                candidate
                for candidate in candidates
                if normalize_event_name(candidate.name) == normalized
                and event_dates_overlap(
                    start_date, end_date, candidate.start_date, candidate.end_date
                )
            ),
            key=lambda candidate: candidate.event_id,
        )
    )


def event_series_slug(name: str) -> str:
    """Return the recurring-series slug used by canonical identifiers."""
    return series_slug(name)
