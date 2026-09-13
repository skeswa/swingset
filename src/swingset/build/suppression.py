"""Publication suppression across structural records and streamed change history."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Iterable, Iterator, Mapping, Sequence
from typing import Any

from swingset.build.schema import PRIMARY_KEYS

PERSONAL_FIELDS = frozenset(
    {
        "name_raw",
        "name_norm",
        "first_name",
        "last_name",
        "initials",
        "city_raw",
        "country_raw",
        "wsdc_id",
    }
)


def _text(value: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().casefold()
    return " ".join(re.findall(r"[a-z0-9]+", ascii_text))


def _decoded(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            pass
    return value


def _key(table: str, row: Mapping[str, Any]) -> str:
    return json.dumps(
        [row.get(field) for field in PRIMARY_KEYS.get(table, ())],
        default=str,
        separators=(",", ":"),
    )


def _change_key(row: Mapping[str, Any]) -> tuple[str, str]:
    return str(row.get("table")), json.dumps(
        _decoded(row.get("record_key")), default=str, separators=(",", ":")
    )


class SuppressionPolicy:
    """Retain only suppression selectors and affected keys, never private notes.

    Discover historical keys before emitting history. Filtering drops affected
    history rows without changing sort keys, so it composes with a stream merge.
    """

    def __init__(self, suppressions: Sequence[Mapping[str, Any]]):
        self.ids = {
            int(row["wsdc_id"]) for row in suppressions if row.get("wsdc_id") not in (None, "")
        }
        self.names = {_text(str(row["name_norm"])) for row in suppressions if row.get("name_norm")}
        self.subjects: set[str] = set()
        self.remapped: dict[str, str] = {}
        self.history_keys: set[tuple[str, str]] = set()
        self._history_names: dict[tuple[str, str], dict[str, set[str]]] = {}

    @property
    def active(self) -> bool:
        return bool(self.ids or self.names)

    def _name_match(self, value: Any) -> bool:
        if not isinstance(value, str):
            return False
        normalized = f" {_text(value)} "
        return any(f" {name} " in normalized for name in self.names if name)

    def _sensitive(self, value: Any, field: str | None = None) -> bool:
        if field and "wsdc_id" in field and value not in (None, ""):
            try:
                if int(value) in self.ids:
                    return True
            except (ValueError, TypeError):
                pass
        if isinstance(value, Mapping):
            return any(self._sensitive(item, str(key)) for key, item in value.items())
        if isinstance(value, (list, tuple)):
            return any(self._sensitive(item, field) for item in value)
        if isinstance(value, str):
            if value in self.subjects or self._name_match(value):
                return True
            parsed = _decoded(value)
            if isinstance(parsed, (dict, list)):
                return self._sensitive(parsed, field)
        return False

    def _record_match(self, table: str, row: Mapping[str, Any]) -> bool:
        if row.get("wsdc_id") in self.ids:
            return True
        identifier = row.get("entry_id") or row.get("judge_id")
        if identifier in self.subjects or (table, _key(table, row)) in self.history_keys:
            return True
        return any(
            self._name_match(row.get(field)) for field in ("name_raw", "name_norm")
        ) or self._name_match(
            " ".join(str(row.get(field) or "") for field in ("first_name", "last_name"))
        )

    def _remember_subject(self, table: str, row: Mapping[str, Any]) -> None:
        self.history_keys.add((table, _key(table, row)))
        identifier = row.get("entry_id") or row.get("judge_id")
        if identifier:
            self.subjects.add(str(identifier))
        if row.get("wsdc_id") is not None:
            self.ids.add(int(row["wsdc_id"]))
        for field in ("name_raw", "name_norm"):
            if row.get(field):
                self.names.add(_text(str(row[field])))
        full = " ".join(str(row.get(field) or "") for field in ("first_name", "last_name")).strip()
        if full:
            self.names.add(_text(full))

    def discover_history(self, history: Iterable[Mapping[str, Any]]) -> bool:
        """Return whether a pass discovered more affected identity metadata.

        Reopen and scan history to a fixed point before emitting any rows. The
        caller bounds passes and fails closed if it cannot reach that point.
        """
        if not self.active:
            return False
        before = (
            len(self.ids),
            len(self.names),
            len(self.subjects),
            len(self.history_keys),
            sum(len(values) for parts in self._history_names.values() for values in parts.values()),
        )
        for row in history:
            table, key = _change_key(row)
            key_values = _decoded(row.get("record_key"))
            key_fields = PRIMARY_KEYS.get(table, ())
            keyed = (
                dict(zip(key_fields, key_values, strict=False))
                if isinstance(key_values, list)
                else {}
            )
            old, new = _decoded(row.get("old_value")), _decoded(row.get("new_value"))
            field = str(row.get("field") or "")
            if (
                (table, key) in self.history_keys
                or self._sensitive(keyed)
                or self._sensitive(old, field)
                or self._sensitive(new, field)
            ):
                self.history_keys.add((table, key))
                for value in (old, new):
                    if isinstance(value, dict) and table in {"entries", "judges", "dancers"}:
                        self._remember_subject(table, value)
                if table in {"entries", "judges", "dancers"}:
                    for value in (old, new):
                        if isinstance(value, str) and field in {"name_raw", "name_norm"} and value:
                            self.names.add(_text(value))
                        if (
                            isinstance(value, str)
                            and field in {"first_name", "last_name"}
                            and value
                        ):
                            self._history_names.setdefault((table, key), {}).setdefault(
                                field, set()
                            ).add(value)
                    parts = self._history_names.get((table, key), {})
                    for first in parts.get("first_name", set()):
                        for last in parts.get("last_name", set()):
                            self.names.add(_text(first + " " + last))
                if table in {"entries", "judges"} and key_values:
                    self.subjects.add(str(key_values[0]))

        after = (
            len(self.ids),
            len(self.names),
            len(self.subjects),
            len(self.history_keys),
            sum(len(values) for parts in self._history_names.values() for values in parts.values()),
        )
        return before != after

    def _scrub_value(self, value: Any, field: str) -> Any:
        if isinstance(value, str) and value in self.subjects:
            return self.remapped.get(value, value)
        if field.endswith("_id") and "wsdc_id" not in field and self._name_match(value):
            raise ValueError("suppressed name occurs in an unsupported structural identifier")
        if self._sensitive(value, field):
            return None
        return value

    def apply(self, rows: Mapping[str, list[dict[str, Any]]]) -> None:
        if not self.active:
            return
        if rows.get("changelog"):
            for _pass in range(32):
                if not self.discover_history(rows["changelog"]):
                    break
            else:
                raise ValueError("suppression history discovery did not converge")
        # Identity names discovered by an ID suppression also cover unlinked appearances.
        while True:
            before = (len(self.ids), len(self.names), len(self.subjects))
            for table in ("dancers", "entries", "judges"):
                for row in rows.get(table, []):
                    if self._record_match(table, row):
                        self._remember_subject(table, row)
            if before == (len(self.ids), len(self.names), len(self.subjects)):
                break
        for identifier in self.subjects:
            if (
                self._name_match(identifier)
                or "/judge/" in identifier
                or "/L-name-" in identifier
                or "/F-name-" in identifier
                or "/C-name-" in identifier
            ):
                self.remapped[identifier] = (
                    "suppressed-"
                    + hashlib.sha256(
                        ("swingset-suppressed-subject-v1|" + identifier).encode()
                    ).hexdigest()[:24]
                )
        for table in ("dancers", "registry_placements"):
            rows.get(table, [])[:] = [
                row for row in rows.get(table, []) if row.get("wsdc_id") not in self.ids
            ]
        for table in ("identity_links", "link_candidates"):
            rows.get(table, [])[:] = [
                row
                for row in rows.get(table, [])
                if str(row.get("subject_id")) not in self.subjects
                and row.get("wsdc_id") not in self.ids
            ]
        for table in ("entries", "judges"):
            for row in rows.get(table, []):
                subject_id = row.get("entry_id") or row.get("judge_id")
                if subject_id not in self.subjects:
                    continue
                for field in PERSONAL_FIELDS:
                    if field in row:
                        row[field] = None
                if "link_status" in row:
                    row["link_status"] = "suppressed"
                if "link_confidence" in row:
                    row["link_confidence"] = 0.0
        for row in rows.get("placements", []):
            changed = False
            for role in ("leader", "follower"):
                if (
                    row.get(f"{role}_entry_id") in self.subjects
                    or row.get(f"{role}_wsdc_id") in self.ids
                    or row.get("couple_entry_id") in self.subjects
                ):
                    row[f"{role}_wsdc_id"] = None
                    row[f"registry_points_{role}"] = None
                    changed = True
            if changed:
                row["registry_confirmed"] = False
                row["points_matches_expected"] = None
        for table, table_rows in rows.items():
            if table == "changelog":
                continue
            for row in table_rows:
                for field, value in tuple(row.items()):
                    row[field] = self._scrub_value(value, field)
        if "changelog" in rows:
            self.discover_history(rows["changelog"])
            rows["changelog"][:] = self.filter_changelog(rows["changelog"])

    def filter_changelog(self, history: Iterable[Mapping[str, Any]]) -> Iterator[dict[str, Any]]:
        """Drop private deltas, preserving unaffected rows and their sort order."""
        for row in history:
            if not self.active:
                yield dict(row)
                continue
            table, key = _change_key(row)
            if (table, key) in self.history_keys:
                continue
            key_values = _decoded(row.get("record_key"))
            keyed = (
                dict(zip(PRIMARY_KEYS.get(table, ()), key_values, strict=False))
                if isinstance(key_values, list)
                else {}
            )
            field = str(row.get("field") or "")
            if (
                self._sensitive(keyed)
                or self._sensitive(_decoded(row.get("old_value")), field)
                or self._sensitive(_decoded(row.get("new_value")), field)
            ):
                continue
            yield dict(row)


def apply_suppressions(
    rows: Mapping[str, list[dict[str, Any]]], suppressions: Sequence[Mapping[str, Any]]
) -> SuppressionPolicy:
    policy = SuppressionPolicy(suppressions)
    policy.apply(rows)
    return policy
