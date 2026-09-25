"""Pure source adapters. The adapter tables live in `registry`."""

from .base import (
    Observation,
    ObservationScope,
    PageKind,
    ParseContext,
    ParseResult,
    Source,
    WatchSpec,
)
from .registry import get_page_kind, seed_watches, sources

__all__ = [
    "Observation",
    "ObservationScope",
    "PageKind",
    "ParseContext",
    "ParseResult",
    "Source",
    "WatchSpec",
    "get_page_kind",
    "seed_watches",
    "sources",
]
