"""Restore accepted observation caches without repeating admission side effects.

Normal parse cutoff deliberately keeps an already accepted generation without
replaying its writes. Restore therefore reconstructs this cache explicitly
from accepted immutable evidence before ordinary workers resume. Projection,
link and build work still runs through the normal pipeline.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

from swingset.sources.base import ParseContext
from swingset.sources.phase_one import PHASE1_KINDS

from .body_packs import source_body
from .db import Database
from .objects import Store
from .observations import observation_rows
from .work import WorkUnit, bump_revision, enqueue

_COLUMNS = (
    "observation_id",
    "watch_id",
    "snapshot_id",
    "kind",
    "scope_kind",
    "scope_id",
    "seq",
    "extract_version",
    "parser_version",
    "payload_json",
)


def rehydrate_observations(
    database: Database, *, watch_ids: Sequence[str] | None = None, enqueued_at: str
) -> dict[str, Any]:
    """Resume safely under restore's exclusive lock; write only regenerated cache.

    Each snapshot owns its latest accepted, non-revoked interpretation when
    removal_authority=none. Watch-owned sources use the unit's current selected
    generation. This preserves an older found registry response after a newer
    not-found response, and each independent archived listing capture. Source
    units with no accepted generation continue through ordinary parse workers.
    """
    if not database.holds_writer_lock:
        raise RuntimeError("observation recovery requires the exclusive restore lock")
    from .history_catalog import REHYDRATABLE_SOURCE_PREDICATE

    conn = database.connection
    condition = "" if watch_ids is None else " AND u.watch_id=ANY(%s)"
    values = () if watch_ids is None else (list(watch_ids),)
    candidates = conn.execute(
        """WITH candidates AS (
 SELECT g.generation_id,u.watch_id,g.recipe_json::jsonb #>> '{context,snapshot_id}' AS snapshot_id,
 coalesce((SELECT max(d.decision_id) FROM evidence.admission_decisions d WHERE d.generation_id=g.generation_id AND d.state='accepted'),0) AS accepted_order,g.created_at
 FROM evidence.source_generations g JOIN evidence.source_units u USING(unit_key)
 WHERE 
 """
        + REHYDRATABLE_SOURCE_PREDICATE
        + condition
        + """
), latest AS (
 SELECT DISTINCT ON(watch_id,snapshot_id) generation_id,watch_id,snapshot_id
 FROM candidates ORDER BY watch_id,snapshot_id,accepted_order DESC,created_at DESC,generation_id DESC
)
SELECT generation_id,watch_id,snapshot_id FROM latest ORDER BY watch_id,snapshot_id""",
        values,
    ).fetchall()
    counts = {
        "generations": len(candidates),
        "written_generations": 0,
        "observations": 0,
        "unchanged_generations": 0,
    }
    witness = hashlib.sha256()
    store = Store(database.state_dir)
    for identifier, watch, snapshot in candidates:
        witness.update(str(identifier).encode() + b"\0")
        with database.transaction() as tx:
            found = tx.execute(
                'SELECT g.recipe_json,s.via,w.kind FROM evidence.source_generations g LEFT JOIN "fetch".snapshots s ON s.snapshot_id=%s AND s.watch_id=%s JOIN "fetch".watches w ON w.watch_id=%s WHERE g.generation_id=%s',
                (snapshot, watch, watch, identifier),
            ).fetchone()
            if found is None or found[1] is None:
                raise RuntimeError("accepted source evidence has no retained snapshot context")
            recipe = json.loads(found[0])
            context = ParseContext(**recipe["context"])
            if context.watch_id != watch or context.snapshot_id != snapshot:
                raise RuntimeError("accepted source context differs from its retained unit")
            body = source_body(tx, str(identifier), store=store)
            if body is None:
                raise RuntimeError("accepted source evidence is absent")
            from swingset.admission.generations import deserialize_result

            result = deserialize_result(body.result_json)
            archive_index = (
                found[1] == "wayback"
                and found[2] == "index"
                and context.kind in PHASE1_KINDS
                and context.kind.split(".")[0] == context.source
            )
            rows = observation_rows(
                context,
                result,
                extract_version=recipe["extract_version"],
                parser_version=recipe["parser_version"],
                archive_index=archive_index,
            )
            old = [
                tuple(row)
                for row in tx.execute(
                    "SELECT "
                    + ",".join(_COLUMNS)
                    + " FROM evidence.observations WHERE watch_id=%s AND snapshot_id=%s ORDER BY seq",
                    (watch, snapshot),
                )
            ]
            if old == rows:
                counts["unchanged_generations"] += 1
                continue
            tx.execute(
                "DELETE FROM evidence.observations WHERE watch_id=%s AND snapshot_id=%s",
                (watch, snapshot),
            )
            tx.executemany(
                "INSERT INTO evidence.observations("
                + ",".join(_COLUMNS)
                + ") VALUES("
                + ",".join("%s" for _ in _COLUMNS)
                + ")",
                rows,
            )
            scopes = {WorkUnit("project", str(row[4]), str(row[5])) for row in [*old, *rows]}
            if any(scope.unit_kind in {"calendar", "source_index"} for scope in scopes):
                scopes.add(WorkUnit("project", "map", "all"))
            enqueue(tx, scopes, enqueued_at=enqueued_at)
            bump_revision(tx, "observations")
            counts["written_generations"] += 1
            counts["observations"] += len(rows)
    return {
        "complete": True,
        **counts,
        "selected_generation_sha256": witness.hexdigest(),
        "remaining_unaccepted_units": "ordinary parse workers",
    }


def invalidate_materializations(
    database: Database,
    *,
    enqueued_at: str,
    units: Sequence[WorkUnit] | None = None,
    inventory: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """After the exact history audit, invalidate active cache certificates.

    Keep the historical generation as the ordinary cutoff witness. A staged
    restore cannot treat it as a live materialization until an ordinary worker
    actually replays its output and certifies the cache again. Retired and build
    anchors and every immutable generation remain intact.
    """
    if not database.holds_writer_lock:
        raise RuntimeError("materialization recovery requires the exclusive restore lock")
    if not database.connection.restore_pending:
        raise RuntimeError("materialization recovery requires a staged restore marker")
    from .derivation_dependencies import retired

    wanted = None if units is None else set(units)
    candidates = [
        WorkUnit(str(stage), str(kind), str(identifier))
        for stage, kind, identifier in database.connection.execute(
            "SELECT stage,unit_kind,unit_id FROM derive.derivation_scopes WHERE stage IN ('project','link') ORDER BY stage,unit_kind,unit_id"
        )
    ]
    for entry in inventory or ():
        unit = WorkUnit(str(entry["stage"]), str(entry["unit_kind"]), str(entry["unit_id"]))
        if unit.stage not in {"project", "link"} or retired(unit):
            raise ValueError("restore inventory contains a history-only or unsupported unit")
        if unit not in candidates:
            with database.transaction() as tx:
                tx.execute(
                    "INSERT INTO derive.derivation_scopes(stage,unit_kind,unit_id,registered_at) VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                    (unit.stage, unit.unit_kind, unit.unit_id, enqueued_at),
                )
            candidates.append(unit)
    queued = []
    changed = 0
    for unit in candidates:
        if retired(unit) or (wanted is not None and unit not in wanted):
            continue
        with database.transaction() as tx:
            result = tx.execute(
                "UPDATE derive.derivation_scopes SET desired_fingerprint=NULL,materialized_signature=NULL WHERE stage=%s AND unit_kind=%s AND unit_id=%s AND (desired_fingerprint IS NOT NULL OR materialized_signature IS NOT NULL)",
                (unit.stage, unit.unit_kind, unit.unit_id),
            )
            changed += max(0, result.rowcount)
            enqueue(tx, (unit,), enqueued_at=enqueued_at)
        queued.append(unit)
    return {
        "complete": True,
        "invalidated_anchors": changed,
        "queued_units": len(queued),
        "preserved_history_anchors": "all build and retired units; immutable generation rows unchanged",
    }


def bootstrap(
    database: Database, *, enqueued_at: str, inventory: Sequence[Mapping[str, Any]] | None = None
) -> dict[str, Any]:
    """Restore-only cache reset after exact audit and stale-admission recovery."""
    observations = rehydrate_observations(database, enqueued_at=enqueued_at)
    materializations = invalidate_materializations(
        database, enqueued_at=enqueued_at, inventory=inventory
    )
    return {"complete": True, "observations": observations, "materializations": materializations}


def _required_closure(
    database: Database, inventory: Sequence[Mapping[str, Any]] | None
) -> tuple[set[WorkUnit], set[str]]:
    """Follow ordinary derivation selections from the captured materializations."""
    from . import derivations
    from .derivation_dependencies import retired

    conn = database.connection
    pending = [
        WorkUnit(str(item["stage"]), str(item["unit_kind"]), str(item["unit_id"]))
        for item in inventory or ()
    ]
    if inventory is None:
        pending = [
            WorkUnit(str(row[0]), str(row[1]), str(row[2]))
            for row in conn.execute(
                "SELECT stage,unit_kind,unit_id FROM derive.derivation_scopes WHERE stage IN ('project','link') AND materialized_generation_id IS NOT NULL"
            )
        ]
    required: set[WorkUnit] = set()
    sources: set[str] = set()
    while pending:
        unit = pending.pop()
        if unit in required or unit.stage not in {"project", "link"} or retired(unit):
            continue
        required.add(unit)
        selected = derivations.desired(conn, unit)
        dependencies = [
            *selected.dependencies,
            *(item for group in selected.dependency_sets.values() for item in group),
        ]
        for item in dependencies:
            if item["kind"] == "derivation":
                pending.append(WorkUnit(*item["key"]))
            elif item["kind"] == "source_selection" and item.get("generation_id") is not None:
                sources.add(str(item["key"]))
    return required, sources


def _reconcile_approved_sources(
    database: Database,
    source_keys: set[str],
    *,
    bundle: Any,
    clock: Any,
    run_id: str,
    deadline: float,
) -> dict[str, Any]:
    """Re-key selected evidence only after exact extract, parse and report proof.

    This is the same differential verifier and recipe constructor as the
    explicit graph-verify/re-key job. It never admits a generation or consumes
    a newer/unapproved snapshot hint.
    """
    import time

    from swingset.fetch.archive import Archive, canonical, digest

    from . import rekey, verify_graph
    from .recipes import capture_runtime

    conn = database.connection
    binding = verify_graph.context(conn)
    runtime = capture_runtime()
    candidate_code = digest(runtime["recipes/runtime.json"])
    names: set[str] = set()
    for key, kind in conn.execute(
        "SELECT u.unit_key,g.page_kind FROM evidence.source_units u JOIN evidence.source_generations g ON g.generation_id=u.accepted_generation_id WHERE u.unit_key=ANY(%s) AND g.state='accepted'",
        (sorted(source_keys),),
    ):
        names.update(stage + "/" + str(kind) + "/" + str(key) for stage in ("extract", "parse"))
    if len(names) != 2 * len(source_keys):
        raise RuntimeError("required source has no accepted generation")
    if rekey._automatic_bumps(database, names):
        raise RuntimeError(
            "approved source declared transform changed or legacy code witness is missing"
        )
    proof = []
    recipes = []
    for key in sorted(source_keys):
        if time.monotonic() >= deadline:
            raise TimeoutError("approved source verification exceeded the rebuild budget")
        found = conn.execute(
            "SELECT g.generation_id,g.page_kind FROM evidence.source_units u JOIN evidence.source_generations g ON g.generation_id=u.accepted_generation_id WHERE u.unit_key=%s AND g.state='accepted'",
            (key,),
        ).fetchone()
        if found is None:
            raise RuntimeError("required source has no accepted generation")
        for stage in ("extract", "parse"):
            checked = verify_graph._source(
                database, stage + "/" + str(found[1]) + "/" + key, Archive(database.state_dir)
            )
            if checked["state"] != "match" or checked["generation_id"] != str(found[0]):
                raise RuntimeError(
                    "required approved source differs from its retained extract, parse or report"
                )
            proof.append(checked)
        recipes.append((key, *rekey._source_recipe(conn, key)))
    if (
        verify_graph.context(conn) != binding
        or digest(capture_runtime()["recipes/runtime.json"]) != candidate_code
    ):
        raise RuntimeError("approved source verification code or input binding changed")
    with database.transaction() as tx:
        for key, generation, fingerprint, recipe in recipes:
            pointer = tx.execute(
                "SELECT accepted_generation_id FROM evidence.source_units WHERE unit_key=%s", (key,)
            ).fetchone()
            if pointer is None or pointer[0] != generation:
                raise RuntimeError("approved source selection changed during verification")
            tx.execute(
                "INSERT INTO evidence.source_equivalences(unit_key,input_fingerprint,generation_id,recipe_json,work_token,created_at,run_id) VALUES(%s,%s,%s,%s,NULL,%s,%s) ON CONFLICT DO NOTHING",
                (key, fingerprint, generation, recipe, clock.now().isoformat(), run_id),
            )
            tx.execute(
                "UPDATE evidence.source_units SET desired_fingerprint=%s WHERE unit_key=%s",
                (fingerprint, key),
            )
    return {
        "complete": True,
        "units": len(source_keys),
        "comparisons": len(proof),
        "source_input_bundle_hash": bundle.digest,
        "candidate_runtime_manifest_sha256": candidate_code,
        "accepted_context_sha256": digest(canonical(binding)),
        "unit_set_sha256": digest(canonical(sorted(source_keys))),
        "exact_source_comparisons_sha256": digest(canonical(proof)),
        "generation_ids": "preserved",
        "admission_decisions": "preserved",
    }


def rebuild(
    database: Database,
    bundle: Any,
    clock: Any,
    *,
    seconds: float = 540,
    max_units: int = 100_000,
    bootstrap_cache: bool = True,
    inventory: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Drain ordinary local derivations; incomplete recovery keeps its marker.

    The outer restore protocol recovers admissions and advances the pressure
    epoch before calling this, and verifies/downloads published baseline files.
    No acquisition, release loop, publication or transport runs here. Build
    history anchors are preserved; project/link caches use ordinary workers.
    Persist bootstrap completion before resuming a bounded partial drain.
    """
    import time

    from swingset.fetch.archive import Archive
    from swingset.schedule.derive import derive_one

    from . import derivations
    from .inputs import accept
    from .work import next_work

    if not database.holds_writer_lock or seconds <= 0 or max_units < 1:
        raise ValueError("rebuild requires the exclusive restore lock and positive bounds")
    started = time.monotonic()
    roots = inventory
    if roots is None:
        roots = [
            dict(zip(("stage", "unit_kind", "unit_id"), row, strict=True))
            for row in database.connection.execute(
                "SELECT stage,unit_kind,unit_id FROM derive.derivation_scopes WHERE stage IN ('project','link') AND materialized_generation_id IS NOT NULL"
            )
        ]
    initial = (
        bootstrap(database, enqueued_at=clock.now().isoformat(), inventory=inventory)
        if bootstrap_cache
        else None
    )
    # Re-accept the exact retained bytes to initialize discarded input counters.
    accept(database, bundle, clock, rehydrate=True)
    run_id = database.start_run(clock.now())
    required, sources = _required_closure(database, roots)
    source_proof = _reconcile_approved_sources(
        database, sources, bundle=bundle, clock=clock, run_id=run_id, deadline=started + seconds
    )
    families = sorted({(unit.stage, unit.unit_kind) for unit in required})
    excluded: set[WorkUnit] = set()
    completed = 0
    outcomes: dict[str, int] = {}
    reason = None
    while completed < max_units and time.monotonic() - started < seconds:
        selected = None
        try:
            for stage, kind in families:
                selected = next_work(
                    database.connection,
                    stage,
                    now=clock.now(),
                    exclude=excluded,
                    allowed=required.__contains__,
                    unit_kind=kind,
                )
                if selected is not None:
                    break
        except Exception as error:
            reason = "rebuild_selection_failed"
            outcomes[type(error).__name__] = outcomes.get(type(error).__name__, 0) + 1
            break
        if selected is None:
            break
        outcome = derive_one(database, Archive(database.state_dir), selected, bundle, clock, run_id)
        code = outcome.reason or ("unit_failed" if outcome.failed else "output_committed")
        outcomes[code] = outcomes.get(code, 0) + 1
        completed += 1
        if outcome.failed or outcome.reason is not None:
            excluded.add(selected)
    remaining = [
        unit
        for unit in sorted(required, key=lambda item: (item.stage, item.unit_kind, item.unit_id))
        if not derivations.current(database.connection, unit)
    ]
    keys = [(unit.stage, unit.unit_kind, unit.unit_id) for unit in required]
    columns = tuple([key[index] for key in keys] for index in range(3))
    unrelated = database.connection.execute(
        "WITH needed AS(SELECT unnest(%s::text[]) AS stage,unnest(%s::text[]) AS unit_kind,unnest(%s::text[]) AS unit_id) SELECT p.stage,p.unit_kind,count(*) AS obligations FROM derive.pending_work p WHERE NOT EXISTS(SELECT 1 FROM needed n WHERE (n.stage,n.unit_kind,n.unit_id)=(p.stage,p.unit_kind,p.unit_id)) GROUP BY p.stage,p.unit_kind ORDER BY p.stage,p.unit_kind",
        columns,
    ).fetchall()
    if remaining and reason is None:
        reason = (
            "rebuild_budget_exhausted"
            if completed >= max_units or time.monotonic() - started >= seconds
            else "rebuild_blocked"
        )
    mismatches = []
    if not remaining and reason is None:
        from .verify import _live_label

        for expected in inventory or ():
            unit = WorkUnit(
                str(expected["stage"]), str(expected["unit_kind"]), str(expected["unit_id"])
            )
            if _live_label(database.connection, unit) != (
                expected["output_digest"],
                expected["row_count"],
            ):
                mismatches.append("/".join((unit.stage, unit.unit_kind, unit.unit_id)))
        if mismatches:
            reason = "rebuilt_materialization_differs"
    complete = not remaining and reason is None
    return {
        "complete": complete,
        "run_id": run_id,
        "bootstrap": initial,
        "processed_units": completed,
        "required_materializations": len(required),
        "approved_source_proof": source_proof,
        "preserved_unrelated_pending": [
            {"stage": row[0], "unit_kind": row[1], "units": row[2]} for row in unrelated
        ],
        "remaining_units": len(remaining),
        "outcomes": outcomes,
        "reason": reason,
        "seconds": round(time.monotonic() - started, 3),
        "input_bundle_hash": bundle.digest,
        "materialization_mismatches": mismatches,
        "build_anchors": "preserved; public baseline files verified by the outer restore protocol",
    }
