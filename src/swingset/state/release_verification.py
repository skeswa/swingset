"""Release authorization after a runtime audit change (D-0264)."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from swingset.state import engine


def _scope(unit: str) -> str:
    parts = unit.split("/", 2)
    return "recipe/code/" + (parts[0] if parts[0] == "link" else "/".join(parts[:2]))


def _baseline(conn: engine.Connection) -> dict[str, Any] | None:
    for key in ("audit_verified_context", "audit_bootstrap_context"):
        row = conn.execute("SELECT value FROM meta WHERE key = %s", (key,)).fetchone()
        if row is not None:
            try:
                value = json.loads(row[0])
                if isinstance(value, dict) and isinstance(value.get("audit"), dict):
                    return value
            except (ValueError, TypeError):
                pass
    return None


def _covers(
    receipt: dict[str, Any], baseline: dict[str, Any] | None, audit: dict[str, str]
) -> bool:
    scopes = receipt.get("scopes")
    if scopes is None:
        return True
    if (
        not isinstance(scopes, list)
        or any(not isinstance(s, str) for s in scopes)
        or baseline is None
    ):
        return False
    before = baseline["audit"]
    changes = {name for name in before.keys() | audit.keys() if before.get(name) != audit.get(name)}
    # Build stays byte-based and has its existing publication comparison.
    changes.discard("recipe/code/build")
    if any(not name.startswith("recipe/code/") for name in changes):
        return False  # a runtime change affects every transform
    selected = {"recipe/code/" + s.removeprefix("recipe/code/") for s in scopes}
    return changes <= selected


def _coverage(conn: engine.Connection, receipt: dict[str, Any]) -> bool:
    """Every old selected output is proved; newer outputs must be current."""
    from . import derivations
    from .verify_graph import enumerate_units
    from .work import WorkUnit

    started = datetime.fromisoformat(receipt["started_at"])
    if started.tzinfo is None:
        return False
    results = {r["unit"]: r for r in receipt["results"]}
    tables = {
        row[0]
        for row in conn.execute(
            "SELECT relname AS name FROM pg_catalog.pg_class WHERE relkind = 'r' AND (PG_TABLE_IS_VISIBLE(oid) AND relnamespace NOT IN ('pg_catalog'::regnamespace,'information_schema'::regnamespace))"
        )
    }
    if "derivation_scopes" not in tables and "source_units" not in tables:
        return not results
    for name in enumerate_units(conn, receipt.get("scopes")):
        stage, kind, identifier = name.split("/", 2)
        if stage in {"project", "link"}:
            row = conn.execute(
                (
                    "SELECT g.generation_id, g.created_at FROM derivation_scopes AS s JOIN "
                    "derivation_generations AS g ON g.generation_id = s.materialized_generation_id WHERE s.stage "
                    "= %s AND s.unit_kind = %s AND s.unit_id = %s"
                ),
                (stage, kind, identifier),
            ).fetchone()
        else:
            row = conn.execute(
                (
                    "SELECT g.generation_id, g.created_at FROM source_units AS u JOIN source_generations AS g ON "
                    "g.generation_id = u.accepted_generation_id WHERE u.unit_key = %s"
                ),
                (identifier,),
            ).fetchone()
        if row is None:
            return False
        proved = results.get(name)
        if proved is not None and proved["generation_id"] == str(row[0]):
            continue
        created = datetime.fromisoformat(str(row[1]))
        if created.tzinfo is None or created <= started:
            return False
        if stage in {"project", "link"}:
            if not derivations.current(conn, WorkUnit(stage, kind, identifier)):
                return False
        else:
            # A source absent from the proof needs the same settled currency
            # checks as a repaired source. This path is conservative: a new
            # scoped proof can include it without waiting for other repairs.
            if not _sources_current(conn):
                return False
    return True


def _sources_current(conn: engine.Connection) -> bool:
    from swingset.admission.generations import parse_inputs
    from swingset.admission.select import source_materialized_for

    inputs = dict(
        conn.execute("SELECT input_name, digest FROM accepted_inputs WHERE consumer = 'pipeline'")
    )
    for row in conn.execute(
        "SELECT u.unit_key, u.accepted_generation_id, u.desired_fingerprint, g.recipe_json, "
        "g.page_kind FROM source_units AS u JOIN source_generations AS g ON g.generation_id = "
        "u.accepted_generation_id"
    ):
        identifier, generation, fingerprint, original, kind = row
        if not fingerprint or not source_materialized_for(
            conn, identifier, generation, fingerprint
        ):
            return False
        equivalent = conn.execute(
            (
                "SELECT recipe_json FROM source_equivalences WHERE unit_key = %s AND generation_id = %s AND "
                "input_fingerprint = %s"
            ),
            (identifier, generation, fingerprint),
        ).fetchone()
        recipe = json.loads((equivalent or (original,))[0])
        if tuple(tuple(item) for item in recipe.get("accepted_inputs", [])) != parse_inputs(
            conn, kind
        ):
            return False
        if any(
            str(recipe.get(label + "_version")) != str(inputs.get(f"version/{label}/{kind}"))
            for label in ("extract", "parser")
        ):
            return False
    return True


def _drained(conn: engine.Connection, receipt: dict[str, Any]) -> bool:
    """A proven version bump is repairable only after ordinary work settles.

    Verify records the bump from the stored recipe, independently of the
    author's declared exclusions. Currentness checks follow consumer read sets
    and prevent a repaired producer from releasing stale dependent output.
    """
    from . import derivations
    from .verify_graph import enumerate_units
    from .work import WorkUnit

    differences = [r for r in receipt["results"] if r["state"] == "mismatch"]
    if not differences:
        return False
    for result in differences:
        unit = result["unit"]
        stage, kind, _ = unit.split("/", 2)
        eligible = (
            {f"version/{'extract' if stage == 'extract' else 'parser'}/{kind}"}
            if stage in {"parse", "extract"}
            else set()
        )
        if result.get("stored_legacy_recipe") is False:
            eligible.add(_scope(unit))
        stored = result.get("stored_versions", {})
        if not isinstance(stored, dict) or not any(
            name in stored
            and name in receipt["versions"]
            and stored[name] != receipt["versions"][name]
            for name in eligible
        ):
            return False
    if (
        conn.execute(
            "SELECT 1 AS _value_0 FROM pending_work WHERE stage IN ('parse', 'project', 'link') LIMIT 1"
        ).fetchone()
        is not None
    ):
        return False
    sources = False
    for name in enumerate_units(conn):
        stage, kind, identifier = name.split("/", 2)
        if stage in {"project", "link"}:
            if not derivations.current(conn, WorkUnit(stage, kind, identifier)):
                return False
        else:
            sources = True
    return not sources or _sources_current(conn)


def blocked(conn: engine.Connection, state_dir: Path) -> dict[str, Any] | None:
    """Only release waits; an incomplete or stale proof authorizes no work.

    A scoped proof must cover every scope changed since the last trusted
    context, including multiple acceptances. Initial empty graphs establish
    their context without replaying nonexistent units.
    """
    if (
        conn.execute(
            "SELECT 1 AS _value_0 FROM accepted_inputs WHERE consumer = 'audit' LIMIT 1"
        ).fetchone()
        is None
    ):
        return None
    from .verify_graph import context, validate_receipt

    live = context(conn)
    trusted = {
        "audit": live["audit"],
        "versions": {k: v for k, v in live["versions"].items() if k.startswith("recipe/code/")},
    }
    baseline = _baseline(conn)
    if baseline == trusted:
        return None
    path = state_dir / "verification" / "receipt.json"
    try:
        receipt = json.loads(path.read_text())
        if not validate_receipt(receipt, audit=live["audit"]):
            return {"reason": "incomplete_or_stale_receipt", "receipt": str(path)}
        if receipt.get("versions") != live["versions"]:
            return {"reason": "declared_versions_changed", "receipt": str(path)}
        if not _coverage(conn, receipt):
            return {"reason": "unverified_current_units", "receipt": str(path)}
        if not _covers(receipt, baseline, live["audit"]):
            return {"reason": "unverified_audit_scopes", "receipt": str(path)}
        if not receipt.get("clean") and not _drained(conn, receipt):
            return {"reason": "verification_differences_or_pending_repairs", "receipt": str(path)}
    except (OSError, ValueError, TypeError, KeyError, engine.Error):
        return {"reason": "missing_or_invalid_receipt", "receipt": str(path)}
    conn.execute(
        (
            "INSERT INTO meta (key, value) VALUES ('audit_verified_context', %s) ON CONFLICT(key) DO UPDATE SET value = excluded.value"
        ),
        (json.dumps(trusted, sort_keys=True),),
    )
    return None
