"""Typed source-vocabulary observations and their storage codec."""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol, cast, get_args, get_origin, get_type_hints

from .enums import ScopeKind

JsonValue = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]


class Observation(Protocol):
    @property
    def kind(self) -> str: ...


_DECODERS: dict[str, Callable[[Mapping[str, JsonValue]], Observation]] = {}


def register_observation_type[T: Observation](cls: type[T]) -> type[T]:
    """Register a frozen dataclass as an observation payload type."""
    if not dataclasses.is_dataclass(cls):
        raise TypeError("observation types must be dataclasses")
    kind_field = next((field for field in dataclasses.fields(cls) if field.name == "kind"), None)
    if kind_field is None:
        raise TypeError("observation types need a kind field")
    kind = cls.__name__
    # Source records use an explicit instance kind. Registration by conventional
    # snake case is only a fallback; decoders are also learned during encoding.
    kind = "".join(("_" + char.lower()) if char.isupper() else char for char in kind).lstrip("_")
    if kind in _DECODERS:
        raise ValueError(f"duplicate observation kind: {kind}")
    _DECODERS[kind] = lambda values: _construct(cls, values)
    return cls


observation_type = register_observation_type


@dataclass(frozen=True, slots=True)
class ObservationEnvelope:
    observation_id: str
    watch_id: str
    snapshot_id: str
    kind: str
    scope_kind: ScopeKind
    scope_id: str
    seq: int
    extract_version: str
    parser_version: str
    payload: Observation


def encode_payload(payload: Observation) -> str:
    if not dataclasses.is_dataclass(payload):
        raise TypeError("observation payload must be a dataclass")
    _DECODERS.setdefault(payload.kind, lambda values: _construct(type(payload), values))
    return json.dumps(
        dataclasses.asdict(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )


def decode_payload(kind: str, payload_json: str) -> Observation:
    decoder = _DECODERS.get(kind)
    if decoder is None:
        raise ValueError(f"unknown observation kind: {kind}")
    value = json.loads(payload_json)
    if not isinstance(value, dict):
        raise ValueError("observation payload must be a JSON object")
    return decoder(cast(Mapping[str, JsonValue], value))


def _construct[T](cls: type[T], values: Mapping[str, JsonValue]) -> T:
    hints = get_type_hints(cls)
    return cls(**{name: _convert(hints.get(name), value) for name, value in values.items()})


def _convert(hint: object, value: JsonValue) -> Any:
    if hint is None or value is None:
        return value
    origin = get_origin(hint)
    args = get_args(hint)
    if origin in (tuple, list) and isinstance(value, list):
        member = args[0] if args else None
        converted = [_convert(member, item) for item in value]
        return tuple(converted) if origin is tuple else converted
    if isinstance(hint, type) and dataclasses.is_dataclass(hint) and isinstance(value, dict):
        return _construct(hint, value)
    return value
