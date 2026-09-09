"""Pure source adapters."""

from typing import cast

from .base import (
    Observation,
    ObservationScope,
    PageKind,
    ParseContext,
    ParseResult,
    Source,
    WatchSpec,
)


def sources() -> tuple[Source, ...]:
    from .eepro import SOURCE as eepro
    from .scoringdance import SOURCE as scoringdance
    from .wdr import SOURCE as wdr
    from .wsdc_calendar import SOURCE as calendar
    from .wsdc_registry import SOURCE as registry

    return tuple(cast(Source, item) for item in (calendar, registry, eepro, scoringdance, wdr))


def get_page_kind(name: str) -> PageKind:
    for source in sources():
        page_kinds = source.page_kinds
        if name in page_kinds:
            return page_kinds[name]
    raise KeyError(f"unknown page kind: {name}")


def seed_watches(config: object, overrides: object) -> list[WatchSpec]:
    result: list[WatchSpec] = []
    for source in sources():
        result.extend(source.seed_watches(config, overrides))
    return result


__all__ = [
    "Observation",
    "ObservationScope",
    "ParseContext",
    "ParseResult",
    "WatchSpec",
    "get_page_kind",
    "seed_watches",
    "sources",
]
