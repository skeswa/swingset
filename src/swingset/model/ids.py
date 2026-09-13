"""Deterministic public and internal identifiers."""

import hashlib
import json
import re
import unicodedata
from collections.abc import Iterable
from datetime import UTC, date, datetime


def slug(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = "".join(char for char in normalized if not unicodedata.combining(char))
    result = re.sub(r"[^a-z0-9]+", "-", ascii_value.casefold()).strip("-")
    return result or "unknown"


def unique_slugs(values: Iterable[str]) -> list[str]:
    """Slug values in source order, suffixing collisions from two onward."""
    seen: dict[str, int] = {}
    result: list[str] = []
    for value in values:
        base = slug(value)
        seen[base] = seen.get(base, 0) + 1
        result.append(base if seen[base] == 1 else f"{base}-{seen[base]}")
    return result


def series_id(name: str, registry_event_id: int | None = None) -> str:
    return (
        f"wsdc-{registry_event_id}"
        if registry_event_id is not None
        else f"slug-{series_slug(name)}"
    )


def event_id(end_date: date, series_name: str, occurrence: int = 1) -> str:
    month = end_date.strftime("%Y-%m")
    base = f"{month}-{series_slug(series_name)}"
    return base if occurrence == 1 else f"{base}-{occurrence}"


def series_slug(name: str) -> str:
    """Normalize an event edition name to its recurring series slug."""
    value = re.sub(r"\(?\b(?:on\s+)?hiatus\b\)?", " ", name, flags=re.IGNORECASE)
    value = re.sub(r"\b20\d{2}\b", " ", value)
    value = re.sub(r"\b\d{1,2}(?:st|nd|rd|th)\b", " ", value, flags=re.IGNORECASE)
    value = re.sub(r"\b[ivx]+\b", " ", value, flags=re.IGNORECASE)
    return slug(value)


def contest_id(event: str, name: str, occurrence: int = 1) -> str:
    base = f"{event}/{slug(name)}"
    return base if occurrence == 1 else f"{base}-{occurrence}"


def round_id(contest: str, round_type: str, occurrence: int = 1) -> str:
    base = f"{contest}/{slug(round_type)}"
    return base if occurrence == 1 else f"{base}-{occurrence}"


def entry_id(contest: str, role: str, bib: str | None, name: str | None = None) -> str:
    letter = {"leader": "L", "follower": "F", "couple": "C"}.get(role.casefold(), role[:1].upper())
    token = bib.strip() if bib is not None and bib.strip() else f"name-{slug(name or 'unknown')}"
    return f"{contest}/{letter}-{token}"


def heat_id(round_: str, number: int) -> str:
    return f"{round_}/heat-{number}"


def judge_id(event: str, name: str | None = None, anonymous_number: int | None = None) -> str:
    token = f"anon-{anonymous_number}" if anonymous_number is not None else slug(name or "unknown")
    return f"{event}/judge/{token}"


def placement_id(round_: str, place: int) -> str:
    return f"{round_}/place-{place}"


def _compact(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def snapshot_id(fetched_at: datetime, body_sha256: str, *, watch_key: str | None = None) -> str:
    # Distinct hosts may return identical bodies within the same second.
    # Keep their acquisition/provenance envelopes distinct while bodies dedupe.
    suffix = "_" + hashlib.sha256(watch_key.encode()).hexdigest()[:12] if watch_key else ""
    return f"snap_{_compact(fetched_at)}_{body_sha256[:12]}{suffix}"


def run_id(started_at: datetime) -> str:
    return f"run_{_compact(started_at)}"


def watch_id(source: str, kind: str, method: str, url: str, form: object | None = None) -> str:
    canonical_form = (
        ""
        if form is None
        else json.dumps(form, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    )
    raw = "|".join((source, kind, method.upper(), url, canonical_form))
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def observation_id(watch: str, snapshot: str, kind: str, seq: int) -> str:
    raw = f"{watch}|{snapshot}|{kind}|{seq}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]
