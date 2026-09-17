"""Pinned source-event denominators and selected-support page coverage.

Selected support and bounded local verification are separate. A local stage
total is known only when every pinned request was assessed. Scheduling pressure
observations never enter this proof or its public counts.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Any

from swingset.schedule.event_evidence import (
    admission_reason,
    decode_generation,
    request,
    request_id,
)

from .closure_manifest import ClosureError, digest

FORMAT = "source-event-release-v1"
MAX_EVENTS = 1024
MAX_MEMBERS = 4096
MAX_TOTAL_MEMBERS = 32768
MAX_JSON_BYTES = 1024 * 1024
MAX_TOTAL_BYTES = 32 * 1024 * 1024
MAX_WITNESS_BYTES = 32 * 1024 * 1024

PUBLIC_FIELDS = {
    "event_id": "string",
    "enumeration_id": "string",
    "enumeration_snapshot_ids": "strings",
    "enumeration_complete": "bool",
    "listed_pages": "int",
    "acquired_pages": "int",
    "acquisition_unknown_pages": "int",
    "interpretation_unknown_pages": "int",
    "interpreted_pages": "int",
    "selected_interpreted_pages": "int",
    "represented_pages": "int",
    "unavailable_pages": "int",
    "unsupported_pages": "int",
}


class _Unavailable(ValueError):
    pass


def _instant(value: str) -> datetime:
    at = datetime.fromisoformat(value)
    if at.tzinfo is None:
        raise _Unavailable("event_evidence_time_unknown")
    return at


class _Reader:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.bytes = self.members = 0
        self.columns: dict[str, list[str]] = {}
        self.revoked: list[tuple[Any, ...]] | None = None
        self.parents: dict[tuple[str, str], dict[str, Any]] = {}

    def rows(
        self, table: str, where: str, parameters: tuple[Any, ...], *, limit: int
    ) -> list[dict[str, Any]]:
        if table not in self.columns:
            self.columns[table] = [
                row[1] for row in self.conn.execute(f'PRAGMA table_info("{table}")')
            ]
        columns = self.columns[table]
        size = "+".join(f'coalesce(length(CAST("{c}" AS BLOB)),0)' for c in columns)
        fields = ",".join(f'CASE WHEN ({size})<=? THEN "{c}" END AS "{c}"' for c in columns)
        bound = min(MAX_JSON_BYTES, MAX_TOTAL_BYTES - self.bytes)
        if bound <= 0:
            raise _Unavailable("event_evidence_byte_budget")
        cursor = self.conn.execute(
            f'SELECT {fields},({size}) AS _bytes FROM "{table}" WHERE {where} LIMIT ?',
            (*([bound] * len(columns)), *parameters, limit + 1),
        )
        result: list[dict[str, Any]] = []
        try:
            for row in cursor:
                if len(result) == limit:
                    raise _Unavailable("event_evidence_row_budget")
                if row["_bytes"] > bound:
                    raise _Unavailable("event_evidence_byte_budget")
                self.bytes += row["_bytes"]
                if self.bytes > MAX_TOTAL_BYTES:
                    raise _Unavailable("event_evidence_byte_budget")
                result.append({key: row[key] for key in columns})
        finally:
            cursor.close()
        return result

    def enumeration(self, identifier: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        rows = self.rows("source_event_enumerations", "enumeration_id=?", (identifier,), limit=1)
        if not rows:
            raise _Unavailable("event_enumeration_missing")
        row = rows[0]
        members = self.rows(
            "source_event_enumeration_members",
            "enumeration_id=? ORDER BY request_id",
            (identifier,),
            limit=min(MAX_MEMBERS, MAX_TOTAL_MEMBERS - self.members),
        )
        self.members += len(members)
        decoded = [
            {
                "request_id": member["request_id"],
                "request": json.loads(member["request_json"]),
                "support": json.loads(member["support_json"]),
                "first_known_at": member["first_known_at"],
            }
            for member in members
        ]
        content = {
            "source": row["source"],
            "source_ref": row["source_ref"],
            "predecessor": row["predecessor_id"],
            "generation_id": row["generation_id"],
            "parents": json.loads(row["parent_support_json"]),
            "members": decoded,
            "pagination": row["pagination"],
        }
        if (
            "enumeration_" + digest(content) != identifier
            or digest([member["request_id"] for member in members]) != row["membership_digest"]
        ):
            raise _Unavailable("event_enumeration_content_changed")
        if any(request_id(member["request"]) != member["request_id"] for member in decoded):
            raise _Unavailable("event_request_identity_changed")
        return row, members

    def parent(self, identifier: str, cutoff: str) -> dict[str, Any]:
        key = (identifier, cutoff)
        if key in self.parents:
            return self.parents[key]
        rows = self.rows("source_generations", "generation_id=?", (identifier,), limit=1)
        if not rows:
            raise _Unavailable("event_parent_missing")
        value = decode_generation(rows[0], identifier)
        if _instant(value["created_at"]) > _instant(cutoff):
            raise _Unavailable("event_parent_after_cutoff")
        if self.revoked is None:
            revoked = self.rows(
                "source_generations", "state='revoked' ORDER BY generation_id", (), limit=128
            )
            self.revoked = [
                (r["unit_key"], r["input_fingerprint"], r["recipe_json"]) for r in revoked
            ]
        reason = admission_reason(self.conn, value, revoked_rows=self.revoked)
        if reason:
            raise _Unavailable(reason)
        manifest = value["manifest"]
        if not isinstance(manifest, list) or not manifest or len(manifest) > MAX_MEMBERS:
            raise _Unavailable("event_parent_manifest_unavailable")
        snapshots = []
        for member in manifest:
            records = self.rows("snapshots", "snapshot_id=?", (member["snapshot_id"],), limit=1)
            if not records or any(
                records[0][key] != member[key] for key in ("watch_id", "url", "body_sha256")
            ):
                raise _Unavailable("event_parent_snapshot_changed")
            if _instant(records[0]["fetched_at"]) > _instant(cutoff):
                raise _Unavailable("event_parent_snapshot_after_cutoff")
            snapshots.append(
                {key: member[key] for key in ("snapshot_id", "watch_id", "url", "body_sha256")}
            )
        immutable = {
            key: value[key]
            for key in (
                "generation_id",
                "input_fingerprint",
                "recipe",
                "manifest",
                "report",
                "result",
            )
        }
        receipt = {
            "generation_id": identifier,
            "content_digest": digest(immutable),
            "snapshots": snapshots,
        }
        self.parents[key] = receipt
        return receipt


def _selected(
    reader: _Reader, selected_support: Iterable[Mapping[str, Any]], cutoff: str
) -> dict[str, dict[str, Any]]:
    selected: dict[str, dict[str, Any]] = {}
    seen: set[str] = set()
    for item in selected_support:
        if (
            item.get("state") != "accepted"
            or not item.get("source_generations")
            or item.get("scope", [None])[0] != "source_event"
        ):
            continue
        snapshot = str(item["snapshot_id"])
        if snapshot in seen:
            continue
        seen.add(snapshot)
        records = reader.rows("snapshots", "snapshot_id=?", (snapshot,), limit=1)
        if not records:
            continue
        record = records[0]
        if _instant(record["fetched_at"]) > _instant(cutoff):
            continue
        # source is owned by the admitted interpretation, not mutable watch metadata.
        sources = {receipt["recipe"]["context"]["source"] for receipt in item["source_generations"]}
        for source in sources:
            key = request_id(request(source, record["method"], record["url"], record["form"]))
            selected.setdefault(key, {"snapshot_ids": [], "via": set()})
            selected[key]["snapshot_ids"].append(snapshot)
            selected[key]["via"].add(record["via"])
    return selected


def capture(
    conn: sqlite3.Connection, *, cutoff: str, selected_support: Iterable[Mapping[str, Any]]
) -> dict[str, Any] | None:
    """Capture immutable denominators and exact selected admissible page support."""
    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE name='source_event_inventory'"
    ).fetchone():
        return None
    _instant(cutoff)
    eligible = "(first_known_at IS NULL OR julianday(first_known_at) IS NULL OR julianday(first_known_at)<=julianday(?))"
    total = conn.execute(
        f"SELECT count(*) FROM source_event_inventory WHERE {eligible}", (cutoff,)
    ).fetchone()[0]
    if not total:
        return None
    reader = _Reader(conn)
    support_unknown = False
    try:
        selected = _selected(reader, selected_support, cutoff)
    except (ValueError, KeyError, TypeError, RecursionError):
        selected, support_unknown = {}, True
    entries = []
    output_bytes = 0
    subjects = conn.execute(
        f"SELECT source,source_ref,enumeration_id FROM source_event_inventory WHERE {eligible} ORDER BY source,source_ref LIMIT ?",
        (cutoff, MAX_EVENTS),
    )
    for source, source_ref, current in subjects:
        entry: dict[str, Any] = {
            "source": source,
            "source_ref": source_ref,
            "enumeration_id": None,
            "reasons": [],
            "members": [],
            "parents": [],
        }
        if support_unknown:
            entry["reasons"].append("selected_support_unassessed")
        try:
            if not current:
                raise _Unavailable("legacy_enumeration_unassessed")
            for _ in range(MAX_MEMBERS):
                enum, members = reader.enumeration(current)
                if _instant(enum["recorded_at"]) <= _instant(cutoff):
                    break
                current = enum["predecessor_id"]
                if not current:
                    raise _Unavailable("event_enumeration_after_cutoff")
            else:
                raise _Unavailable("event_predecessor_budget")
            entry.update(
                enumeration_id=current,
                enumeration_digest=digest(enum),
                members_digest=digest(members),
                listed_pages=len(members),
                pagination=enum["pagination"],
            )
            parents = json.loads(enum["parent_support_json"])
            for identifier in parents:
                try:
                    entry["parents"].append(reader.parent(identifier, cutoff))
                except (ValueError, KeyError, TypeError, RecursionError) as exc:
                    if isinstance(exc, _Unavailable) and str(exc).endswith("_budget"):
                        raise
                    entry["reasons"].append(
                        str(exc) if isinstance(exc, _Unavailable) else "event_parent_invalid"
                    )
            for member in members:
                support = selected.get(member["request_id"], {})
                entry["members"].append(
                    {
                        "request_id": member["request_id"],
                        "snapshot_ids": sorted(set(support.get("snapshot_ids", ()))),
                        "via": sorted(support.get("via", ())),
                    }
                )
            if not parents:
                entry["reasons"].append("event_parent_missing")
        except (ValueError, KeyError, TypeError, RecursionError) as exc:
            # A partial bounded read is not a positive proof. Discard its prefix
            # so verification never needs more budget than successful capture.
            entry = {
                "source": source,
                "source_ref": source_ref,
                "enumeration_id": None,
                "reasons": [],
                "members": [],
                "parents": [],
            }
            entry["reasons"].append(
                str(exc) if isinstance(exc, _Unavailable) else "event_enumeration_invalid"
            )
        entry["reasons"] = sorted(set(entry["reasons"]))
        encoded_bytes = len(json.dumps(entry, ensure_ascii=False).encode())
        if output_bytes + encoded_bytes > MAX_WITNESS_BYTES:
            entry = {
                "source": source,
                "source_ref": source_ref,
                "enumeration_id": None,
                "reasons": ["event_witness_byte_budget"],
                "members": [],
                "parents": [],
            }
            encoded_bytes = len(json.dumps(entry, ensure_ascii=False).encode())
            if output_bytes + encoded_bytes > MAX_WITNESS_BYTES:
                break  # The omitted-subject count discloses the remaining fleet.
        output_bytes += encoded_bytes
        entries.append(entry)
    value = {
        "format": FORMAT,
        "cutoff": cutoff,
        "entries": entries,
        "omitted_subjects": max(0, total - len(entries)),
    }
    from .event_local_coverage import capture as capture_local

    local = capture_local(conn, entries, cutoff=cutoff)
    if local is not None:
        value["local_pages"] = local
    return {**value, "digest": digest(value)}


def validate(
    conn: sqlite3.Connection,
    witness: Mapping[str, Any],
    *,
    selected_support: Iterable[Mapping[str, Any]],
    local_budget: Any = None,
) -> None:
    if witness.get("format") != FORMAT or digest(
        {k: v for k, v in witness.items() if k != "digest"}
    ) != witness.get("digest"):
        raise ClosureError("event_coverage_witness_invalid")
    reader = _Reader(conn)
    try:
        selected = _selected(reader, selected_support, witness["cutoff"])
    except (ValueError, KeyError, TypeError, RecursionError):
        selected = {}
    for entry in witness["entries"]:
        identifier = entry["enumeration_id"]
        if identifier is None:
            if (
                entry.get("listed_pages") is not None
                or entry.get("members")
                or entry.get("parents")
                or entry.get("pagination") is not None
            ):
                raise ClosureError("unassessed_event_coverage_has_positive_support")
            continue  # Explicitly unassessed; no positive denominator or support.
        try:
            enum, members = reader.enumeration(identifier)
            if _instant(enum["recorded_at"]) > _instant(witness["cutoff"]):
                raise _Unavailable("event_enumeration_after_cutoff")
            if any(
                entry.get(key) != enum[key] for key in ("source", "source_ref", "pagination")
            ) or entry.get("listed_pages") != len(members):
                raise _Unavailable("event_enumeration_summary_changed")
            if not entry["reasons"] and {p["generation_id"] for p in entry["parents"]} != set(
                json.loads(enum["parent_support_json"])
            ):
                raise _Unavailable("event_parent_support_omitted")
            if (
                digest(enum) != entry["enumeration_digest"]
                or digest(members) != entry["members_digest"]
            ):
                raise _Unavailable("event_enumeration_changed")
            if "selected_support_unassessed" not in entry["reasons"]:
                expected = [
                    {
                        "request_id": m["request_id"],
                        "snapshot_ids": sorted(
                            set(selected.get(m["request_id"], {}).get("snapshot_ids", ()))
                        ),
                        "via": sorted(selected.get(m["request_id"], {}).get("via", ())),
                    }
                    for m in members
                ]
                if expected != entry["members"]:
                    raise _Unavailable("selected_event_pages_changed")
            for parent in entry["parents"]:
                if reader.parent(parent["generation_id"], witness["cutoff"]) != parent:
                    raise _Unavailable("event_parent_changed")
        except (ValueError, KeyError, TypeError, RecursionError) as exc:
            raise ClosureError("event_coverage_support_changed") from exc

    from .event_local_coverage import validate as validate_local

    validate_local(conn, witness, local_budget)


def rows(
    witness: Mapping[str, Any],
    *,
    selected_mapping: Iterable[Mapping[str, Any]],
    schemas: Mapping[str, Any],
) -> list[dict[str, Any]]:
    mapping = {(item["source"], item["source_ref"]): item["event_id"] for item in selected_mapping}
    result = []
    for entry in witness["entries"]:
        from .event_local_coverage import FORMAT as LOCAL_FORMAT
        from .event_local_coverage import UNAVAILABLE_FORMAT, summary

        totals = summary(entry, witness.get("local_pages"))
        reasons = [*entry["reasons"]]
        if totals["acquired_pages"] is None or totals["interpreted_pages"] is None:
            reasons.append("full_cutoff_local_verification_unavailable")
        unavailable_assessed = (witness.get("local_pages") or {}).get("format") in (
            LOCAL_FORMAT,
            UNAVAILABLE_FORMAT,
        )
        if unavailable_assessed and totals["unavailable_pages"] is None:
            reasons.append("source_unavailability_unassessed")
        if totals["unsupported_pages"] is None:
            reasons.append("source_unsupported_unassessed")
        event_id = mapping.get((entry["source"], entry["source_ref"]))
        if event_id is None:
            reasons.append("canonical_mapping_unresolved")
        row: dict[str, Any] = {name: None for name in schemas["coverage"].names}
        row.update(
            scope_kind="source_event",
            scope_id=entry["source_ref"],
            source=entry["source"],
            via="unknown",
            event_id=event_id,
            year=None,
            enumeration_id=entry["enumeration_id"],
            enumeration_snapshot_ids=sorted(
                {s["snapshot_id"] for p in entry["parents"] for s in p["snapshots"]}
            ),
            enumeration_complete=entry.get("pagination") == "complete"
            if entry["enumeration_id"]
            else None,
            listed_pages=entry.get("listed_pages"),
            selected_interpreted_pages=sum(bool(m["snapshot_ids"]) for m in entry["members"])
            if entry["enumeration_id"] and not entry["reasons"]
            else None,
            represented_pages=0 if entry["enumeration_id"] and not entry["reasons"] else None,
            scope_status="partial",
            scope_reasons=sorted(reasons),
            missing_scopes=[],
            evidence_cutoff=_instant(witness["cutoff"]),
            usable_verified_at=_instant(witness["local_pages"]["verified_at"])
            if witness.get("local_pages") is not None
            else None,
            method=FORMAT,
            population="pinned source-event request obligations; all transports deduplicated",
            discovery_denominator=None,
            discovery_universe="unknown",
            acquisition_denominator=entry.get("listed_pages"),
            interpretation_denominator=entry.get("listed_pages"),
            uncertainty=(
                "Local stage totals describe pinned verification observations; NULL means unassessed members. Unavailable counts require explicit retained origin responses with no usable acquired support; zero does not mean available. Unsupported counts require verified critical-unknown contract evidence; canonical scoring exclusions remain findings. Zero does not imply canonical support. Selected support and final representation are separate; via=unknown aggregates transports."
                if unavailable_assessed
                else "Local stage totals describe pinned verification observations; NULL means unassessed members. Unavailable/unsupported classification remains unassessed. Selected support and final representation are separate; via=unknown aggregates transports."
            ),
            **totals,
        )
        result.append(row)
    return result


def finalize(tables: Mapping[str, list[dict[str, Any]]], witness: Mapping[str, Any]) -> None:
    """Only final emitted result facts establish candidate representation."""
    result_tables = (
        "contests",
        "rounds",
        "heats",
        "entries",
        "judges",
        "callbacks",
        "callback_marks",
        "final_marks",
        "final_scores",
        "placements",
    )
    snapshots = {
        str(row["snapshot_id"])
        for table in result_tables
        for row in tables.get(table, ())
        if row.get("snapshot_id")
    }
    entries = {(entry["source"], entry["source_ref"]): entry for entry in witness["entries"]}
    for row in tables.get("coverage", ()):
        if row.get("scope_kind") != "source_event":
            continue
        entry = entries.get((row["source"], row["scope_id"]))
        if entry and row.get("represented_pages") is not None:
            row["represented_pages"] = sum(
                bool(snapshots.intersection(member["snapshot_ids"])) for member in entry["members"]
            )


def fingerprint(conn: sqlite3.Connection, witness: Mapping[str, Any]) -> str | None:
    """Hash bounded raw evidence; proof caches retain identifiers, not payloads."""
    reader = _Reader(conn)
    values: list[str] = []
    try:
        for snapshot in witness["snapshots"]:
            values.append(digest(reader.rows("snapshots", "snapshot_id=?", (snapshot,), limit=1)))
        for entry in witness["entries"]:
            if not entry["enumeration_id"]:
                continue
            values.append(
                digest(
                    reader.rows(
                        "source_event_enumerations",
                        "enumeration_id=?",
                        (entry["enumeration_id"],),
                        limit=1,
                    )
                )
            )
            values.append(
                digest(
                    reader.rows(
                        "source_event_enumeration_members",
                        "enumeration_id=? ORDER BY request_id",
                        (entry["enumeration_id"],),
                        limit=MAX_MEMBERS,
                    )
                )
            )
            for parent in entry["parents"]:
                identifier = parent["generation_id"]
                values.append(
                    digest(
                        reader.rows("source_generations", "generation_id=?", (identifier,), limit=1)
                    )
                )
                values.append(
                    digest(
                        bool(
                            conn.execute(
                                "SELECT 1 FROM admission_decisions WHERE generation_id=? AND state='accepted' LIMIT 1",
                                (identifier,),
                            ).fetchone()
                        )
                    )
                )
                for snapshot in parent["snapshot_ids"]:
                    values.append(
                        digest(reader.rows("snapshots", "snapshot_id=?", (snapshot,), limit=1))
                    )
    except (ValueError, KeyError, TypeError, RecursionError):
        return None
    if witness.get("local_pages") is not None:
        from .event_local_coverage import fingerprint as local_fingerprint

        try:
            values.append(local_fingerprint(reader, witness["local_pages"]))
        except (ValueError, KeyError, TypeError, RecursionError):
            return None
    return digest(values)


def read_set(
    witness: Mapping[str, Any], selected_support: Iterable[Mapping[str, Any]] = ()
) -> dict[str, Any]:
    snapshots = {
        snapshot for e in witness["entries"] for m in e["members"] for snapshot in m["snapshot_ids"]
    }
    snapshots.update(
        str(item["snapshot_id"])
        for item in selected_support
        if item.get("state") == "accepted"
        and item.get("source_generations")
        and item.get("scope", [None])[0] == "source_event"
    )
    from .event_local_coverage import read_set as local_read_set

    return {
        **(
            {"local_pages": local_read_set(witness)}
            if witness.get("local_pages") is not None
            else {}
        ),
        "snapshots": sorted(snapshots),
        "entries": [
            {
                "enumeration_id": e["enumeration_id"],
                "parents": [
                    {
                        "generation_id": p["generation_id"],
                        "snapshot_ids": [s["snapshot_id"] for s in p["snapshots"]],
                    }
                    for p in e["parents"]
                ],
            }
            for e in witness["entries"]
            if e["enumeration_id"]
        ],
    }


def semantic_token(witness: Mapping[str, Any]) -> str:
    """A later unchanged cutoff alone does not create a new public release."""
    value = {key: value for key, value in witness.items() if key not in {"cutoff", "digest"}}
    if value.get("local_pages") is not None:
        value["local_pages"] = {
            key: item for key, item in value["local_pages"].items() if key != "verified_at"
        }
    return digest(value)
