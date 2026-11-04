"""Stream the columns owned by each projection or link generation."""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from datetime import datetime
from typing import TYPE_CHECKING, Any

from swingset.model.schema import TABLES
from swingset.state import engine
from swingset.state.work import WorkUnit

VERSION = 1

if TYPE_CHECKING:
    from swingset.state.derivations import OutputRow, Selection

# Processing clocks belong to generation/attempt receipts. Source snapshots and
# source evidence times remain in payloads; replay does not invent fresh evidence.
_PROCESSING = frozenset({"first_seen_at", "last_seen_at", "run_id", "asserted_at"})
_EXTRA_KEYS = {
    "source_events": ("source", "source_ref"),
    "source_event_map": ("source", "source_ref"),
    "source_event_proposals": ("source", "source_ref"),
    "series": ("series_id",),
    "coverage": ("year", "source", "via"),
    "identity_links": ("link_id",),
    "link_candidates": ("subject_kind", "subject_id", "wsdc_id"),
    "identity_link_resolutions": ("subject_kind", "subject_id"),
}
# Columns a year generation owns on rows another scope owns (history's before
# plan F17). The year rewrites them after that scope ran, so recording them
# there would make every re-run of the owner differ from its own generation
# and never cut off (D-0205).
_HISTORY_COLUMNS = {"events": ("coverage_tier",)}
# Kinds whose output never passes through `_owned`: none of their generations
# recorded a history column (inventory left it out before phase 1 too).
_UNOWNED_KINDS = frozenset(
    {"inventory", "history", "year", "mapping", "source_index", "source_event"}
)


def moved_columns(unit: WorkUnit) -> Mapping[str, tuple[str, ...]]:
    """History columns this unit's generations from before phase 1 recorded.

    Their output leaves them out now, so the cutoff comparison takes them out
    of the kept generation too; otherwise the first switch would mint every
    such scope once and, by F1, every link (D-0212).
    """
    if unit.stage == "project" and unit.unit_kind not in _UNOWNED_KINDS:
        return _HISTORY_COLUMNS
    return {}


def _rows(
    conn: engine.Connection,
    table: str,
    *,
    where: str = "TRUE",
    parameters: tuple[Any, ...] = (),
    columns: Iterable[str] | None = None,
    exclude: Iterable[str] = (),
    query: str | None = None,
) -> Iterator[OutputRow]:
    from swingset.state.derivations import OutputRow

    keys = TABLES[table].primary_key if table in TABLES else _EXTRA_KEYS[table]
    wanted = set(columns) | set(keys) if columns is not None else None
    ignored = _PROCESSING | frozenset(exclude)
    projection = ",".join(sorted(wanted - ignored)) if wanted is not None else "*"
    query = query or f"SELECT {projection} FROM {table} WHERE {where} ORDER BY {','.join(keys)}"
    for row in conn.execute(query, parameters):
        payload = {
            key: row[key]
            for key in row.keys()
            if key not in ignored and (wanted is None or key in wanted)
        }
        key = json.dumps([row[column] for column in keys], separators=(",", ":"))
        yield OutputRow(table, key, payload)


def _owned(conn: engine.Connection, kind: str, identifier: str) -> Iterator[OutputRow]:
    tables = conn.execute(
        (
            "SELECT DISTINCT table_name FROM canonical_scope_rows WHERE scope_kind = %s AND scope_id = "
            "%s ORDER BY table_name NULLS FIRST"
        ),
        (kind, identifier),
    ).fetchall()
    for (table,) in tables:
        keys = TABLES[table].primary_key
        query = (
            f"SELECT t.* FROM canonical_scope_rows c JOIN {table} t ON "
            + " AND ".join(
                f"t.{key}::text=(c.record_key::json ->> {index})" for index, key in enumerate(keys)
            )
            + " WHERE c.scope_kind=%s AND c.scope_id=%s AND c.table_name=%s ORDER BY "
            + ",".join(f"t.{key}" for key in keys)
        )
        yield from _rows(
            conn,
            table,
            query=query,
            parameters=(kind, identifier, table),
            exclude=(*TABLES[table].owner_columns, *_HISTORY_COLUMNS.get(table, ())),
        )


def output_rows(conn: engine.Connection, unit: WorkUnit) -> Iterator[OutputRow]:
    """Retain scoped semantic output, including nulls and explicit empty scopes.

    A partial payload owns only its listed columns. For example a year
    generation owns a registry placement's event association while the dancer
    generation retains its source facts. Removed rows are represented by their
    absence from this complete generation's declared scope.
    """
    if unit.stage == "link":
        subject = "subject_id IN (SELECT entry_id FROM entries WHERE event_id=%s UNION SELECT judge_id FROM judges WHERE event_id=%s)"
        for table in ("identity_links", "link_candidates", "identity_link_resolutions"):
            yield from _rows(conn, table, where=subject, parameters=(unit.unit_id, unit.unit_id))
        for table in ("entries", "judges", "placements"):
            yield from _rows(
                conn,
                table,
                where="event_id=%s",
                parameters=(unit.unit_id,),
                columns=TABLES[table].owner_columns,
            )
        return
    if unit.stage != "project":
        raise ValueError(f"no projection output collector for {unit.stage}")
    if unit.unit_kind == "inventory":
        yield from _rows(
            conn,
            "events",
            exclude=("coverage_tier",),
            where="history_source!='[]' OR EXISTS (SELECT 1 FROM canonical_scope_rows c WHERE c.scope_kind='history' AND c.scope_id='all' AND c.table_name='events' AND c.record_key=ops.compact_json(json_build_array(events.event_id::text)))",
        )
        yield from _rows(conn, "series")
    elif unit.unit_kind == "mapping":
        # The source's proposals, placeholder events included (plan F19).
        yield from _rows(
            conn, "source_event_proposals", where="source=%s", parameters=(unit.unit_id,)
        )
    elif unit.unit_kind == "resolution":
        # The event's map rows and its placeholder row.
        yield from _rows(conn, "source_event_map", where="event_id=%s", parameters=(unit.unit_id,))
        yield from _owned(conn, "resolution", unit.unit_id)
    elif unit.unit_kind == "history":
        yield from _rows(conn, "coverage")
        yield from _rows(conn, "events", columns=("coverage_tier",))
        yield from _rows(conn, "registry_placements", columns=("event_id",))
    elif unit.unit_kind == "year":
        # The year's coverage rows, its events' tiers and the association of
        # placements reported in it (plan F17).
        year = int(unit.unit_id)
        yield from _rows(conn, "coverage", where="year=%s", parameters=(year,))
        yield from _rows(
            conn, "events", columns=("coverage_tier",), where="year=%s", parameters=(year,)
        )
        yield from _rows(
            conn,
            "registry_placements",
            columns=("event_id",),
            where="substr(event_month,1,4)=%s",
            parameters=(f"{year:04d}",),
        )
    elif unit.unit_kind == "source_index":
        yield from _rows(
            conn,
            "source_events",
            where="EXISTS (SELECT 1 FROM source_event_scope_rows o WHERE o.scope_id=%s AND o.source=source_events.source AND o.source_ref=source_events.source_ref)",
            parameters=(unit.unit_id,),
        )
    elif unit.unit_kind == "source_event":
        # This dispatch is a proxy. Its event/map generations own the output;
        # its selected dependencies preserve the source-to-canonical route.
        return
    else:
        yield from _owned(conn, unit.unit_kind, unit.unit_id)


def helper_recipe(
    conn: engine.Connection, files: Mapping[str, bytes], stage: str
) -> dict[str, str] | None:
    """Identify explicit helper bundles before a runtime bundle has been accepted.

    Accepted code scopes identify a captured runtime; before that, helper
    bundles can name only the policy files they supply.
    """
    from hashlib import sha256

    from swingset.state.recipes import recipe_inputs

    accepted = recipe_inputs(conn, stage)
    if (
        "recipe/runtime" in accepted
        or conn.execute(
            "SELECT 1 AS _value_0 FROM accepted_inputs WHERE consumer = 'pipeline' AND input_name ILIKE 'recipe/code/%' LIMIT 1"
        ).fetchone()
        is not None
    ):
        return None
    names = (
        {
            "overrides/event_aliases.csv",
            "overrides/source_urls.csv",
            "overrides/series_aliases.csv",
            "overrides/finding_reviews.csv",
        }
        if stage == "project"
        else {"link/weights.toml", "overrides/nicknames.csv", "overrides/identity_overrides.csv"}
    )
    supplied = {
        "helper/" + name: sha256(body).hexdigest() for name, body in files.items() if name in names
    }
    return {**accepted, **supplied} if supplied else None


def continuity_rows(
    conn: engine.Connection, selection: Selection | None, table: str
) -> list[Mapping[str, Any]] | None:
    """Read immutable prior support; None means a legacy, uncaptured helper call."""
    if selection is None:
        return None
    identifier = selection.continuity.get("dependency_set_id")
    if identifier is None:
        return []
    members = selection.dependency_sets.get(str(identifier))
    if members is None:
        row = conn.execute(
            "SELECT manifest_json FROM derivation_dependency_sets WHERE dependency_set_id = %s",
            (identifier,),
        ).fetchone()
        if row is None:
            raise ValueError("captured continuity support is unavailable")
        members = json.loads(row[0])
    return [item["row"] for item in members if item.get("table") == table]


@contextmanager
def materializing(
    conn: engine.Connection,
    unit: WorkUnit,
    *,
    now: datetime | str,
    run_id: str,
    selection: Selection | None = None,
    recipe: Mapping[str, str] | None = None,
    reads: Mapping[str, Any] | None = None,
) -> Iterator[Selection | None]:
    """Capture, write and finish a scope inside its caller's bounded transaction.

    Inside `derive_one`, a large unit alone in its group only writes its live
    rows and a building row here (step A); `derive_one` then stores its output
    in chunks and completes it, deleting its `pending_work` row (WP4).

    A link passes ``reads``, its run's read set with each key's value at
    load: the selection is formed from those keys, and a value that moved
    since the load supersedes the run (plan F20). The admission's selection
    was formed from the previous read set, so only its recipe must match.
    """
    from swingset.state import derivations
    from swingset.state.attempts import SupersededWorkError

    if not derivations.available(conn):
        yield None
        return
    staged = derivations.stageable(conn, unit)
    with derivations.group(conn):
        captured = derivations.capture(
            conn,
            unit,
            now=now,
            recipe=(selection.recipe if selection.explicit_recipe else None)
            if selection
            else recipe,
            context=selection.context if selection else None,
            reads=None if reads is None else tuple(reads),
        )
        if reads is not None:
            derivations.check_reads(conn, captured, reads)
            if selection is not None and dict(captured.recipe) != dict(selection.recipe):
                raise SupersededWorkError("selected recipe changed before linking")
        elif selection is not None and captured.fingerprint != selection.fingerprint:
            raise SupersededWorkError("selected inputs changed before projection or linking")
        yield captured
        if staged and derivations.stage(
            conn,
            captured,
            output=lambda reader: output_rows(reader, unit),
            now=now,
            run_id=run_id,
            moved=moved_columns(unit),
        ):
            return
        # A callable lets complete() read the output twice: once to compare it
        # with the selected generation, and once more to store it if it differs.
        derivations.complete(
            conn,
            captured,
            rows=lambda: output_rows(conn, unit),
            now=now,
            run_id=run_id,
            moved=moved_columns(unit),
        )
        conn.execute(
            "DELETE FROM pending_work WHERE stage = %s AND unit_kind = %s AND unit_id = %s",
            (unit.stage, unit.unit_kind, unit.unit_id),
        )
