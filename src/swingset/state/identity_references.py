"""Original source locators and durable, explicitly reviewed continuity."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any

from swingset.normalize.names import normalize_name, paired_names
from swingset.sources.records import Cell


def fingerprint(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


@dataclass(frozen=True)
class SourceReference:
    source: str
    source_event: str
    contest: str
    round: str
    participant: str

    @property
    def ref_id(self) -> str:
        return fingerprint(asdict(self))


@dataclass(frozen=True)
class ReferenceBinding:
    reference: SourceReference
    subject_kind: str
    subject_id: str
    event_id: str
    snapshot_id: str
    semantic_hash: str
    locator: dict[str, Any]


def references_for_subject(
    conn: sqlite3.Connection, kind: str, identifier: str
) -> tuple[ReferenceBinding, ...]:
    return ReferenceReader(conn).for_subject(kind, identifier)


class ReferenceReader:
    """Locate current or baseline rows in retained source observations once."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        *,
        observation_payloads: Mapping[str, Sequence[str]]
        | Callable[[str], Iterable[str]]
        | None = None,
    ):
        self.conn = conn
        self.observation_payloads = observation_payloads
        self.rows: dict[str, dict[tuple[str, str], list[dict[str, Any]]]] = {}

    def _index(self, snapshot: str) -> dict[tuple[str, str], list[dict[str, Any]]]:
        if snapshot not in self.rows:
            indexed: dict[tuple[str, str], list[dict[str, Any]]] = {}
            payloads = (
                self.observation_payloads(snapshot)
                if callable(self.observation_payloads)
                else self.observation_payloads.get(snapshot, ())
                if self.observation_payloads is not None
                else (
                    record[0]
                    for record in self.conn.execute(
                        "SELECT payload_json FROM observations WHERE snapshot_id=? AND kind='round_sheet'",
                        (snapshot,),
                    )
                )
            )
            for payload in payloads:
                sheet = json.loads(payload)
                for table_number, table in enumerate(sheet.get("tables", [])):
                    base = {
                        "source_event": sheet.get("source_event_ref"),
                        "contest_name": sheet.get("contest_name_raw"),
                        "source_round": sheet["source_round_ref"],
                        "table": table_number,
                    }
                    from swingset.project.contests import _bib_for_role, _competitors, _round_type

                    headers = [
                        str(cell.get("text") or "").strip().casefold()
                        for cell in table.get("headers", [])
                    ]
                    competitor_columns = [
                        index
                        for index, header in enumerate(headers)
                        if any(
                            word in header
                            for word in ("competitor", "leader", "follower", "couple", "dancer")
                        )
                        or header in {"am", "pro"}
                    ]
                    competitor_columns = sorted(
                        set(competitor_columns)
                        | {
                            index
                            for row in table.get("rows", [])
                            for index, cell in enumerate(row.get("cells", []))
                            if "data-wsdc" in dict(cell.get("attributes", []))
                        }
                    )
                    bib_columns = [index for index, header in enumerate(headers) if "bib" in header]
                    source_row = self.conn.execute(
                        "SELECT source FROM watches WHERE watch_id=(SELECT watch_id FROM snapshots WHERE snapshot_id=?)",
                        (snapshot,),
                    ).fetchone()
                    source = str(source_row[0]) if source_row else ""
                    for position, row in enumerate(table.get("rows", [])):
                        cells = tuple(
                            Cell(str(cell.get("text") or "")) for cell in row.get("cells", [])
                        )
                        for competitor_position, column in enumerate(competitor_columns):
                            if column >= len(cells) or column >= len(headers):
                                continue
                            raw = (cells[column].text or "").strip()
                            for role, name in _competitors(
                                raw,
                                headers[column],
                                str(base["contest_name"]),
                                len(competitor_columns),
                                competitor_position,
                            ):
                                # A source cell containing a pair cannot establish either individual's ownership here.
                                if paired_names(raw) and role != "couple":
                                    continue
                                bib = _bib_for_role(
                                    cells,
                                    headers,
                                    bib_columns,
                                    role,
                                    generic_shared=source in {"wdr", "scoringdance"}
                                    and _round_type(str(sheet.get("round_name_raw", ""))) == "final"
                                    and len(competitor_columns) > 1,
                                )
                                attributes = dict(
                                    row.get("cells", [])[column].get("attributes", [])
                                )
                                printed_id = str(attributes.get("data-wsdc") or "")
                                located = {
                                    "source_wsdc_id": int(printed_id)
                                    if printed_id.isdecimal() and int(printed_id) > 0
                                    else None,
                                    **base,
                                    "row": position,
                                    "column": column,
                                    "role": role,
                                    "bib": bib,
                                }
                                indexed.setdefault(
                                    ("entry", normalize_name(name).value), []
                                ).append(located)
                    for position, cell in enumerate(table.get("headers", [])):
                        attrs = dict(cell.get("attributes", []))
                        name = normalize_name(
                            str(attrs.get("title") or cell.get("text") or "")
                        ).value
                        if name:
                            indexed.setdefault(("judge", name), []).append(
                                {**base, "column": position}
                            )
            self.rows[snapshot] = indexed
        return self.rows[snapshot]

    def for_subject(self, kind: str, identifier: str) -> tuple[ReferenceBinding, ...]:
        if kind not in {"entry", "judge"}:
            raise ValueError("identity subject must be entry or judge")
        table = "entries" if kind == "entry" else "judges"
        cursor = self.conn.execute(f"SELECT * FROM {table} WHERE {kind}_id=?", (identifier,))
        row = cursor.fetchone()
        if row is None:
            return ()
        subject = dict(zip((field[0] for field in cursor.description), row, strict=True))
        contest_name = None
        if kind == "entry":
            contest = self.conn.execute(
                "SELECT name_raw FROM contests WHERE contest_id=?", (subject["contest_id"],)
            ).fetchone()
            contest_name = str(contest[0]) if contest else None
        return self.for_record(kind, subject, contest_name=contest_name)

    def for_record(
        self, kind: str, subject: dict[str, Any], *, contest_name: str | None = None
    ) -> tuple[ReferenceBinding, ...]:
        """A baseline record needs its original snapshot, not a current entry ID."""
        from swingset.project.contests import _contest_slug

        metadata = self.conn.execute(
            "SELECT w.source_ref,s.url FROM snapshots s JOIN watches w USING(watch_id) WHERE snapshot_id=?",
            (subject["snapshot_id"],),
        ).fetchone()
        if metadata is None or not metadata[0]:
            return ()
        name = normalize_name(str(subject.get("name_raw") or "")).value
        output = {}
        for row in self._index(str(subject["snapshot_id"])).get((kind, name), []):
            if row["source_event"] != metadata[0] or (
                kind == "entry"
                and (
                    contest_name is None
                    or _contest_slug(row["contest_name"]) != _contest_slug(contest_name)
                )
            ):
                continue
            locator = {"url": metadata[1], "table": row["table"]}
            semantics = {"name": subject.get("name_raw")}
            if kind == "entry":
                bib = str(subject.get("bib") or "")
                if row["role"] != subject["role"] or (bib and bib != row["bib"]):
                    continue
                locator.update(
                    {
                        "row": row["row"],
                        "column": row["column"],
                        "role": subject["role"],
                        "bib": bib,
                        "source_wsdc_id": row["source_wsdc_id"],
                    }
                )
                semantics.update({"role": subject["role"], "bib": bib})
                participant = (
                    f"entry:{subject['role']}:bib:{bib}"
                    if bib
                    else f"entry:{subject['role']}:row:{row['row']}"
                )
            else:
                locator["column"] = row["column"]
                participant = f"judge:column:{row['column']}"
            ref = SourceReference(
                str(subject["source"]),
                str(metadata[0]),
                str(row["source_round"]),
                str(row["table"]),
                participant,
            )
            output[ref.ref_id + fingerprint(locator)] = ReferenceBinding(
                ref,
                kind,
                str(subject[f"{kind}_id"]),
                str(subject["event_id"]),
                str(subject["snapshot_id"]),
                fingerprint(semantics),
                locator,
            )
        return tuple(output[key] for key in sorted(output))


def retain_binding(conn: sqlite3.Connection, binding: ReferenceBinding, *, now: str) -> None:
    ref = binding.reference
    conn.execute(
        "INSERT OR IGNORE INTO identity_source_refs VALUES (?,?,?,?,?,?,?,?,?)",
        (
            ref.ref_id,
            ref.source,
            ref.source_event,
            ref.contest,
            ref.round,
            ref.participant,
            binding.subject_kind,
            json.dumps(binding.locator, sort_keys=True),
            now,
        ),
    )
    values = (
        ref.ref_id,
        binding.subject_kind,
        binding.subject_id,
        binding.event_id,
        binding.snapshot_id,
        binding.semantic_hash,
    )
    conn.execute(
        "INSERT OR IGNORE INTO identity_reference_bindings VALUES (?,?,?,?,?,?,?,?)",
        (fingerprint(values), *values, now),
    )


def record_migration(
    conn: sqlite3.Connection,
    *,
    migration_id: str,
    from_ref_id: str,
    to_ref_ids: tuple[str, ...],
    status: str,
    evidence: str,
    reason: str,
    author: str,
    date: str,
    now: str,
    supersedes: str | None = None,
) -> bool:
    """Append reviewed continuity and invalidation in the caller's transaction."""
    from datetime import date as Date

    from swingset.state.work import WorkUnit, bump_revision, enqueue

    if not conn.in_transaction:
        raise ValueError("reference migration requires an input transaction")
    if not migration_id or not evidence or not reason or not author:
        raise ValueError("reference migration requires review provenance")
    Date.fromisoformat(date)
    if status not in {"approved", "pending", "ambiguous"} or (
        status == "approved" and len(to_ref_ids) != 1
    ):
        raise ValueError("approved continuity requires exactly one target reference")
    for ref in (from_ref_id, *to_ref_ids):
        if (
            conn.execute("SELECT 1 FROM identity_source_refs WHERE ref_id=?", (ref,)).fetchone()
            is None
        ):
            raise ValueError(f"unknown source reference: {ref}")
    self_continuity = from_ref_id in to_ref_ids
    if self_continuity:
        try:
            details = json.loads(evidence)
        except json.JSONDecodeError as exc:
            raise ValueError("same-locator continuity requires reviewed semantic evidence") from exc
        semantic = details.get("semantic_hash") if isinstance(details, dict) else None
        if (
            status != "approved"
            or not isinstance(semantic, str)
            or len(semantic) != 64
            or any(c not in "0123456789abcdef" for c in semantic)
        ):
            raise ValueError("same-locator continuity requires reviewed semantic evidence")
    if supersedes:
        previous = conn.execute(
            "SELECT from_ref_id FROM identity_reference_migrations WHERE migration_id=?",
            (supersedes,),
        ).fetchone()
        if previous is None or previous[0] != from_ref_id:
            raise ValueError("reference migration supersedes an unrelated or missing migration")
    values = (
        migration_id,
        from_ref_id,
        json.dumps(sorted(set(to_ref_ids))),
        status,
        evidence,
        reason,
        author,
        date,
        supersedes,
    )
    existing = conn.execute(
        "SELECT migration_id,from_ref_id,to_ref_ids_json,status,evidence,reason,author,date,supersedes FROM identity_reference_migrations WHERE migration_id=?",
        (migration_id,),
    ).fetchone()
    if existing:
        if tuple(existing) != values:
            raise ValueError("reference migration IDs are immutable")
        return False
    edges = {
        str(row[0]): json.loads(row[1])
        for row in conn.execute(
            "SELECT from_ref_id,to_ref_ids_json FROM identity_reference_migrations WHERE status='approved' AND migration_id NOT IN (SELECT supersedes FROM identity_reference_migrations WHERE supersedes IS NOT NULL)"
        )
    }
    pending = [] if self_continuity else list(to_ref_ids)
    visited = set()
    while pending:
        ref = pending.pop()
        if ref == from_ref_id:
            raise ValueError("cyclic source reference migration")
        if ref in visited:
            continue
        visited.add(ref)
        pending.extend(edges.get(ref, []))
    conn.execute(
        "INSERT INTO identity_reference_migrations VALUES (?,?,?,?,?,?,?,?,?,?)", (*values, now)
    )
    bump_revision(conn, "identity_decisions")
    events = {
        str(row[0])
        for row in conn.execute(
            "SELECT event_id FROM events UNION SELECT event_id FROM identity_reference_bindings"
        )
    }
    enqueue(conn, (WorkUnit("link", "event", event) for event in sorted(events)), enqueued_at=now)
    return True


def approved_reference_path(conn: sqlite3.Connection, origin: str, target: str) -> bool:
    """Only one active approved successor at each step can carry supersession."""
    visited = set()
    while origin != target:
        if origin in visited:
            return False
        visited.add(origin)
        rows = conn.execute(
            "SELECT status,to_ref_ids_json FROM identity_reference_migrations WHERE from_ref_id=? AND migration_id NOT IN (SELECT supersedes FROM identity_reference_migrations WHERE supersedes IS NOT NULL)",
            (origin,),
        ).fetchall()
        if len(rows) != 1 or rows[0][0] != "approved":
            return False
        targets = json.loads(rows[0][1])
        if len(targets) != 1:
            return False
        origin = targets[0]
    return True
