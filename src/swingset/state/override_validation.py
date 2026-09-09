"""Reject malformed operator policies before accepting their input digest."""

import csv
import io
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

_HEADERS = {
    "event_aliases.csv": {"source", "source_ref", "event_id", "note"},
    "identity_overrides.csv": {"entry_id", "wsdc_id", "reason", "author", "date"},
    "nicknames.csv": {"nickname", "canonical"},
    "source_urls.csv": {"event_id", "source", "kind", "url", "parser", "notes"},
    "suppressions.csv": {"reason", "date"},
}
_REQUIRED = {
    "event_aliases.csv": ("source", "source_ref", "event_id"),
    "identity_overrides.csv": ("entry_id", "wsdc_id", "reason", "author", "date"),
    "nicknames.csv": ("nickname", "canonical"),
    "source_urls.csv": ("event_id", "source", "kind", "url", "parser"),
    "suppressions.csv": ("reason", "date"),
}
_KEYS = {
    "event_aliases.csv": ("source", "source_ref"),
    "identity_overrides.csv": ("entry_id",),
    "nicknames.csv": ("nickname",),
    "source_urls.csv": ("source", "url"),
    "suppressions.csv": ("wsdc_id", "name_norm"),
}


def validate_override(path: Path, body: bytes) -> None:
    reader = csv.DictReader(io.StringIO(body.decode("utf-8")))
    headers = reader.fieldnames or []
    if len(headers) != len(set(headers)):
        raise ValueError(f"{path}: duplicate CSV header")
    missing = _HEADERS.get(path.name, set()) - set(headers)
    if missing:
        raise ValueError(f"{path}: missing CSV headers: {', '.join(sorted(missing))}")
    if path.name == "suppressions.csv" and not {"wsdc_id", "name_norm"} & set(headers):
        raise ValueError(f"{path}: suppression requires wsdc_id or name_norm")
    seen: set[tuple[str, ...]] = set()
    for number, row in enumerate(reader, 2):
        try:
            if None in row or any(value is None for value in row.values()):
                raise ValueError("malformed CSV row")
            for name in _REQUIRED.get(path.name, ()):
                if not row[name].strip():
                    raise ValueError(f"empty {name}")
            if row.get("date"):
                date.fromisoformat(row["date"])
            if path.name in ("identity_overrides.csv", "suppressions.csv"):
                wsdc_id = row.get("wsdc_id", "").strip()
                if not wsdc_id and not row.get("name_norm", "").strip():
                    raise ValueError("missing identity")
                if wsdc_id and not (path.name == "identity_overrides.csv" and wsdc_id == "NONE"):
                    if not wsdc_id.isdecimal() or int(wsdc_id) < 1:
                        raise ValueError(
                            "wsdc_id must be positive, or NONE for an identity override"
                        )
            if path.name == "source_urls.csv":
                from swingset.sources import get_page_kind

                url = urlsplit(row["url"])
                if url.scheme != "https" or not url.hostname or url.username or url.password:
                    raise ValueError("source URL must be HTTPS without credentials")
                try:
                    get_page_kind(row["parser"])
                except KeyError as exc:
                    raise ValueError(f"unknown parser {row['parser']}") from exc
                source = {"worlddanceregistry": "wdr"}.get(row["source"], row["source"])
                if row["parser"].split(".")[0] != source:
                    raise ValueError("parser does not belong to source")
            if path.name in _KEYS:
                key = tuple(row.get(name, "").strip().casefold() for name in _KEYS[path.name])
                if key in seen:
                    raise ValueError(f"duplicate policy key {key}")
                seen.add(key)
        except ValueError as exc:
            raise ValueError(f"{path}:{number}: {exc}") from exc
