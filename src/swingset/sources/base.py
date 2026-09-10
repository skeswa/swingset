"""I/O-free contracts shared by source adapters."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal, Protocol

type JsonScalar = None | bool | int | float | str
# Extracts are dynamically decoded trees. Adapters validate their own shape;
# the archive codec enforces canonical JSON at the storage boundary.
type JsonValue = Any
type ChangeMode = Literal["validators", "body_hash", "extract"]
type ScopeKind = Literal["calendar", "source_index", "source_event", "dancer"]


class ObservationPayload(Protocol):
    """Marker protocol implemented structurally by frozen payload records."""

    @property
    def kind(self) -> str: ...


@dataclass(frozen=True)
class ObservationScope:
    kind: ScopeKind
    ref: str


@dataclass(frozen=True)
class Observation:
    scope: ObservationScope
    kind: str
    payload: ObservationPayload


@dataclass(frozen=True)
class ParseWarning:
    code: str
    message: str
    evidence: JsonValue = None


@dataclass(frozen=True)
class ParseContext:
    snapshot_id: str
    watch_id: str
    url: str
    source: str
    kind: str
    source_ref: str | None
    fetched_at: str


@dataclass(frozen=True)
class WatchSpec:
    watch_id: str
    source: str
    kind: str
    method: Literal["GET", "POST"]
    url: str
    parser: str
    form: tuple[tuple[str, str], ...] | None = None
    source_ref: str | None = None
    archive_url: str | None = None
    notes: str | None = None

    def __post_init__(self) -> None:
        # Callers may supply a readable hint; identity always follows the one
        # canonical model function, including canonical form content.
        from swingset.model.ids import watch_id

        object.__setattr__(
            self, "watch_id", watch_id(self.source, self.kind, self.method, self.url, self.form)
        )


@dataclass(frozen=True)
class ParseResult:
    observations: tuple[Observation, ...] = ()
    watches: tuple[WatchSpec, ...] = ()
    warnings: tuple[ParseWarning, ...] = ()
    legitimate_empty: bool = False


class ExtractError(ValueError):
    """The response body does not contain the declared page kind."""


class ParseError(ValueError):
    """A valid extract cannot be interpreted without guessing."""


class PageKind(Protocol):
    kind: str
    EXTRACT_VERSION: int
    PARSER_VERSION: int
    change_mode: ChangeMode

    def extract(self, body: bytes) -> JsonValue: ...

    def parse(self, extract: JsonValue, ctx: ParseContext) -> ParseResult: ...

    def expected_statuses(self, watch: object) -> frozenset[int]: ...


class Source(Protocol):
    name: str
    hosts: frozenset[str]
    page_kinds: Mapping[str, PageKind]

    def seed_watches(self, config: object, overrides: object) -> list[WatchSpec]: ...


def watch_has_success(watch: object) -> bool:
    """Read the one bit WDR needs without coupling sources to state records."""
    for name in ("ever_ok", "has_success", "has_200", "ever_succeeded"):
        value = getattr(watch, name, None)
        if isinstance(value, (bool, int)):
            return bool(value)
    return False
