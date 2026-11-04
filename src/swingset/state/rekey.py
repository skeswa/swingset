"""Record verified recipe equivalences without running any transform.

The caller opens the frozen import with the state-exclusive lock. Receipts
bind inputs and selected generations, rather than the equivalence tables, so
an interrupted batch can resume against precisely the same import.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

from swingset.fetch.archive import canonical, digest
from swingset.state import engine

from . import derivations
from .db import Database
from .work import WorkUnit, enqueue


def _scope(name: str) -> str:
    return "link" if name.startswith("link/") else "/".join(name.split("/")[:2])


def _bound(database: Database, receipt: Mapping[str, Any]) -> None:
    from .verify_graph import checkpoint_identity, context, enumerate_units, validate_receipt

    conn = database.connection
    if not validate_receipt(receipt):
        raise ValueError("re-key requires a complete, valid verify receipt")
    if receipt["checkpoint"] != checkpoint_identity(conn):
        raise ValueError("verify checkpoint differs from this frozen import")
    if receipt["expected_units"] != enumerate_units(conn, receipt.get("scopes")):
        raise ValueError("verify unit set differs from this frozen import")
    actual = context(conn)
    for key in ("audit", "versions", "inputs"):
        if receipt[key] != actual[key]:
            raise ValueError(f"verify {key} differs from this frozen import")


def _dependencies(selection: derivations.Selection) -> list[dict[str, Any]]:
    return [
        *selection.dependencies,
        *(item for group in selection.dependency_sets.values() for item in group),
    ]


def _source_recipe(conn: engine.Connection, key: str) -> tuple[str, str, str]:
    """Use the frozen snapshot context; only accepted recipe values change."""
    from swingset.admission.generations import parse_inputs

    row = conn.execute(
        (
            "SELECT s.accepted_generation_id, g.recipe_json, g.manifest_json, g.page_kind FROM "
            "source_units AS s JOIN source_generations AS g ON g.generation_id = "
            "s.accepted_generation_id WHERE s.unit_key = %s"
        ),
        (key,),
    ).fetchone()
    if row is None:
        raise ValueError(f"source pointer missing: {key}")
    recipe = json.loads(row[1])
    recipe["accepted_inputs"] = parse_inputs(conn, str(row[3]))
    fingerprint = digest(canonical({"manifest": json.loads(row[2]), "recipe": recipe}))
    return str(row[0]), fingerprint, canonical(recipe).decode()


def _automatic_bumps(database: Database, names: set[str]) -> set[str]:
    """Compare stored declared versions, resolving legacy closure identities.

    Before stage 0, the retained scope manifest names captured source bytes.
    New entry-module VERSION constants had no old value: their baseline is 1.
    Missing legacy source refuses an equivalence rather than guessing a version.
    """
    from . import recipe_scope

    conn = database.connection
    current = dict(
        conn.execute("SELECT input_name, digest FROM accepted_inputs WHERE consumer = 'pipeline'")
    )
    legacy: dict[tuple[str, str, str | None], str | None] = {}
    captured_versions: dict[str, dict[str, dict[str, object]]] = {}
    captured_files: dict[str, dict[str, bytes]] = {}
    verified_members: dict[tuple[str, str], str] = {}
    bundles = sorted((database.state_dir / "inputs").glob("*/recipes/scopes.json"))

    def legacy_version(
        scope: str, code: str, *, recorded_contract: str | None = None
    ) -> str | None:
        key = (scope, code, recorded_contract)
        if key in legacy:
            return legacy[key]
        found = None
        for path in bundles:
            manifest = json.loads(path.read_bytes())
            group = manifest.get("scopes", {}).get(scope)
            if not isinstance(group, dict) or digest(canonical(group)) != code:
                continue
            root = path.parent.parent
            bundle_key = str(root)
            if bundle_key not in captured_files:
                files = {
                    "runtime/"
                    + source.relative_to(root / "runtime").as_posix(): source.read_bytes()
                    for source in (root / "runtime").rglob("*")
                    if source.is_file()
                }
                runtime_manifest = root / "recipes/runtime.json"
                if not runtime_manifest.is_file() or json.loads(runtime_manifest.read_bytes()).get(
                    "files"
                ) != {name: digest(body) for name, body in files.items()}:
                    raise ValueError("retained legacy runtime differs from its manifest")
                captured_files[bundle_key] = files
            for member, value in group.items():
                member_key = (bundle_key, member)
                if member_key not in verified_members:
                    actual = recipe_scope.member_digest(captured_files[bundle_key], member)
                    if actual is None:
                        raise ValueError("retained legacy source is missing from its scope")
                    verified_members[member_key] = actual
                if verified_members[member_key] != value:
                    raise ValueError("retained legacy source differs from its scope")
            if bundle_key not in captured_versions:
                files = dict(captured_files[bundle_key])
                # The stage-0 declarations did not exist in old source. Their
                # first declared version is 1; existing declarations are kept.
                for member, body in list(files.items()):
                    if member.endswith(".py"):
                        values = recipe_scope._literals(body)
                        if "VERSION" not in values:
                            files[member] += b"\nVERSION = 1\n"
                        if (
                            member.endswith("/admission/contracts.py")
                            and "PARSE_CORE_VERSION" not in values
                        ):
                            files[member] += b"\nPARSE_CORE_VERSION = 1\n"
                captured_versions[bundle_key] = recipe_scope.declared_versions(files)
            previous = captured_versions[bundle_key].get(scope)
            if previous is not None:
                previous = dict(previous)
                if recorded_contract is not None and scope.startswith("recipe/code/parse/"):
                    from swingset.admission.kinds import CONTRACTS, contract

                    kind = scope.removeprefix("recipe/code/parse/")
                    owner = CONTRACTS.get(kind)
                    module = contract(kind)
                    if owner is not None and module is not None:
                        owner = owner.removeprefix("swingset.")
                        declared = module.VERSIONS.get(kind)
                        # The per-kind module declaration arrived after older
                        # captures. Its old value is witnessed by this immutable
                        # recipe, never inferred from the current version alone.
                        # A declaration actually present in old source wins.
                        if (
                            owner not in previous
                            and not isinstance(declared, bool)
                            and isinstance(declared, (str, int))
                            and str(declared) == recorded_contract
                        ):
                            previous[owner] = declared
                found = digest(canonical(previous))
            break
        legacy[key] = found
        return found

    bumped = set()
    for name in sorted(names):
        stage, kind, identifier = name.split("/", 2)
        scope = "recipe/code/" + _scope(name)
        if stage in {"parse", "extract"}:
            row = conn.execute(
                (
                    "SELECT g.recipe_json, g.contract_version FROM source_units AS s JOIN source_generations AS g ON g.generation_id "
                    "= s.accepted_generation_id WHERE s.unit_key = %s"
                ),
                (identifier,),
            ).fetchone()
            recipe = json.loads(row[0])
            label = "extract" if stage == "extract" else "parser"
            old = recipe.get(label + "_version")
            new = current.get(f"version/{label}/{kind}")
            if old is not None and new is not None and str(old) != new:
                bumped.add(_scope(name))
            # Contract versions are also versions, even when output is equal.
            recorded_contract = None
            if stage == "parse":
                from swingset.admission.kinds import contract_version

                value = recipe.get("contract_version")
                if value is not None:
                    recorded_contract = str(value)
                    if recorded_contract != contract_version(kind) or recorded_contract != str(
                        row[1]
                    ):
                        bumped.add(_scope(name))
                    if recipe.get("context", {}).get("kind") != kind:
                        recorded_contract = None
            old_inputs = {
                entry[1]: entry[2]
                for entry in recipe.get("accepted_inputs", [])
                if entry[0] == "pipeline"
            }
            if scope in old_inputs and scope in current:
                previous = old_inputs[scope]
                if "recipe/env" in old_inputs:
                    previous = legacy_version(scope, previous, recorded_contract=recorded_contract)
                if previous != current[scope]:
                    bumped.add(_scope(name))
            continue
        row = conn.execute(
            (
                "SELECT g.recipe_json FROM derivation_scopes AS s JOIN derivation_generations AS g ON "
                "g.generation_id = s.materialized_generation_id WHERE s.stage = %s AND s.unit_kind = %s AND "
                "s.unit_id = %s"
            ),
            (stage, kind, identifier),
        ).fetchone()
        latest = conn.execute(
            (
                "SELECT r.recipe_json FROM derivation_equivalences AS e JOIN derivation_recipes AS r USING "
                "(recipe_sha256) JOIN derivation_scopes AS s ON s.stage = e.stage AND s.unit_kind = "
                "e.unit_kind AND s.unit_id = e.unit_id AND s.materialized_generation_id = e.generation_id "
                "WHERE e.stage = %s AND e.unit_kind = %s AND e.unit_id = %s ORDER BY e.created_at DESC NULLS "
                "LAST, e.input_fingerprint DESC NULLS LAST LIMIT 1"
            ),
            (stage, kind, identifier),
        ).fetchone()
        old = json.loads((latest or row)[0]).get("inputs", {})
        if scope not in old or scope not in current:
            continue
        previous = old[scope]
        if "recipe/env" in old:
            previous = legacy_version(scope, previous)
        if previous != current[scope]:
            bumped.add(_scope(name))
    return bumped


def run(
    database: Database,
    receipt: Mapping[str, Any],
    *,
    now: datetime,
    batch_size: int = 100,
    stop: Callable[[], bool] = lambda: False,
) -> dict[str, Any]:
    """Re-key clean units in dependency order, committing at most a batch at once.

    Failed verification, missing units and stale bindings refuse the entire
    operation. Differences and explicitly bumped transforms remain queued,
    along with consumers whose dependencies are unresolved. Repeating this
    operation only inserts equivalences that are still absent.
    """
    if not database.holds_writer_lock:
        raise ValueError("re-key requires the state-exclusive lock with loops held")
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    _bound(database, receipt)
    conn = database.connection
    at = now.isoformat()
    expected = set(receipt["expected_units"])
    results = {item["unit"]: item for item in receipt["results"]}
    bumped = set(receipt.get("bumped_transforms", [])) | _automatic_bumps(database, expected)
    clean = {
        name
        for name in expected
        if results[name]["state"] == "match" and _scope(name) not in bumped
    }
    # Extract and parse are one source pointer. Neither can be carried over
    # when the other transform differs or has an explicit version bump.
    source_names: dict[str, set[str]] = {}
    derived: dict[str, WorkUnit] = {}
    for name in sorted(expected):
        stage, kind, identifier = name.split("/", 2)
        if stage in {"parse", "extract"}:
            source_names.setdefault(identifier, set()).add(name)
        elif stage in {"project", "link"}:
            derived[name] = WorkUnit(stage, kind, identifier)
        else:
            raise ValueError(f"unsupported re-key unit: {name}")
    clean_sources = {key for key, names in source_names.items() if names <= clean}
    blocked_sources = set(source_names) - clean_sources
    blocked = set(derived) - clean
    selections = {name: derivations.desired(conn, unit) for name, unit in derived.items()}
    dependencies: dict[str, set[str]] = {}
    for name, selection in selections.items():
        upstream: set[str] = set()
        for item in _dependencies(selection):
            if item["kind"] == "derivation":
                dependency = "/".join(item["key"])
                upstream.add(dependency)
                if dependency not in derived and not derivations.current(
                    conn, WorkUnit(*item["key"])
                ):
                    blocked.add(name)
            elif item["kind"] == "source_selection" and item["key"] in blocked_sources:
                blocked.add(name)
        dependencies[name] = upstream
    # Propagate ineligible producers before writing a single equivalence.
    changed = True
    while changed:
        changed = False
        for name, upstream in dependencies.items():
            if name not in blocked and upstream & blocked:
                blocked.add(name)
                changed = True
    pending = set(derived) - blocked
    order: list[str] = []
    while pending:
        ready = sorted(name for name in pending if not dependencies[name] & pending)
        if not ready:
            raise ValueError("cyclic re-key dependencies")
        order.extend(ready)
        pending.difference_update(ready)
    tasks = [("source", key) for key in sorted(clean_sources)] + [
        ("derive", name) for name in order
    ]
    written: Counter[str] = Counter()
    already_current = 0
    completed = 0
    run_id = database.start_run(now)
    for offset in range(0, len(tasks), batch_size):
        if stop():
            break
        with database.transaction():
            _bound(database, receipt)
            for category, name in tasks[offset : offset + batch_size]:
                if category == "source":
                    generation, fingerprint, recipe_json = _source_recipe(conn, name)
                    if any(
                        results[item]["generation_id"] != generation for item in source_names[name]
                    ):
                        raise ValueError(f"source pointer differs from verify: {name}")
                    cursor = conn.execute(
                        (
                            "INSERT INTO source_equivalences (unit_key, input_fingerprint, generation_id, recipe_json, "
                            "work_token, created_at, run_id) VALUES (%s, %s, %s, %s, NULL, %s, %s) ON CONFLICT DO NOTHING"
                        ),
                        (name, fingerprint, generation, recipe_json, at, run_id),
                    )
                    if cursor.rowcount:
                        written["parse"] += 1
                    else:
                        already_current += 1
                    conn.execute(
                        "UPDATE source_units SET desired_fingerprint = %s WHERE unit_key = %s",
                        (fingerprint, name),
                    )
                    # Work hints reference snapshots, not source units.
                    recipe = json.loads(recipe_json)
                    snapshot = recipe["context"]["snapshot_id"]
                    conn.execute(
                        "DELETE FROM pending_work WHERE stage = 'parse' AND unit_id = %s",
                        (snapshot,),
                    )
                else:
                    unit = derived[name]
                    selected = derivations.selected_generation(conn, unit)
                    if selected is None or selected != results[name]["generation_id"]:
                        raise ValueError(f"derivation pointer differs from verify: {name}")
                    generation = selected
                    selection = derivations.desired(conn, unit)
                    if derivations.materialized_for(
                        conn,
                        (unit.stage, unit.unit_kind, unit.unit_id),
                        generation,
                        selection.fingerprint,
                    ):
                        already_current += 1
                    else:
                        derivations._record_equivalence(
                            conn, selection, str(generation), now=now, run_id=run_id
                        )
                        written[f"{unit.stage}/{unit.unit_kind}"] += 1
                    conn.execute(
                        (
                            "UPDATE derivation_scopes SET desired_fingerprint = %s WHERE stage = %s AND unit_kind = %s "
                            "AND unit_id = %s"
                        ),
                        (selection.fingerprint, unit.stage, unit.unit_kind, unit.unit_id),
                    )
                    conn.execute(
                        "DELETE FROM pending_work WHERE stage = %s AND unit_kind = %s AND unit_id = %s",
                        (unit.stage, unit.unit_kind, unit.unit_id),
                    )
                completed += 1
    with database.transaction():
        _bound(database, receipt)
        enqueue(conn, (derived[name] for name in sorted(blocked)), enqueued_at=at)
        for key in sorted(blocked_sources):
            row = conn.execute(
                (
                    "SELECT g.recipe_json FROM source_units AS s JOIN source_generations AS g ON g.generation_id "
                    "= s.accepted_generation_id WHERE s.unit_key = %s"
                ),
                (key,),
            ).fetchone()
            snapshot = json.loads(row[0])["context"]["snapshot_id"]
            enqueue(conn, [WorkUnit("parse", "snapshot", snapshot)], enqueued_at=at)
    return {
        "format": "swingset-rekey-v1",
        "verify_receipt": digest(canonical(dict(receipt))),
        "checkpoint": receipt["checkpoint"],
        "audit": receipt["audit"],
        "versions": receipt["versions"],
        "run_id": run_id,
        "at": at,
        "complete": completed == len(tasks),
        "equivalences": dict(sorted(written.items())),
        "already_current": already_current,
        "processed": completed,
        "eligible": len(tasks),
        "excluded": sorted(
            blocked | {item for key in blocked_sources for item in source_names[key]}
        ),
        "bumped_transforms": sorted(bumped),
    }
