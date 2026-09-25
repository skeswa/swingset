"""Freeze source inputs and stage immutable generations without selecting output."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, replace
from typing import Any

from swingset import cutoff
from swingset.fetch.archive import Archive, canonical, digest
from swingset.model.observations import decode_payload, encode_payload
from swingset.sources.base import (
    Observation,
    ObservationScope,
    ParseContext,
    ParseResult,
    ParseWarning,
    WatchSpec,
)
from swingset.sources.phase_one import PHASE1_KINDS

from .removal import wdr_preserves_rows
from .report import Guard, Report


@dataclass(frozen=True)
class AttemptInputs:
    unit_key: str
    context: ParseContext
    accepted_inputs: tuple[tuple[str, str, str], ...]
    previous_generation_id: str | None
    work_token: str | None
    extract_version: str
    parser_version: str
    archive_url: str | None = None


def parse_recipe_names(kind: str) -> frozenset[str]:
    """A page kind's parse recipe: environment, history start and its two code scopes.

    `recipe/runtime` is the package-wide identity of bundles captured before
    D-0207; after the first scoped bundle it stays at the absent-input digest.
    This lives here, not in `state.recipes`, so that the parse code scopes do
    not reach the `state` package.
    """
    return frozenset(
        {
            "recipe/env",
            "recipe/runtime",
            "policy/history_start",
            f"recipe/code/extract/{kind}",
            f"recipe/code/parse/{kind}",
        }
    )


def parse_input_names(kind: str) -> frozenset[str]:
    """The parse recipe's names plus the page kind's two version labels."""
    return parse_recipe_names(kind) | {f"version/extract/{kind}", f"version/parser/{kind}"}


def parse_inputs(conn: sqlite3.Connection, kind: str) -> tuple[tuple[str, str, str], ...]:
    """Accepted inputs one page kind's parse depends on, as (consumer, name, digest).

    Source-generation recipes and admission currency use these instead of every
    accepted input (D-0207), so an unrelated input neither grows the recipe nor
    supersedes an in-flight parse.
    """
    selected = parse_input_names(kind)
    return tuple(
        ("pipeline", str(name), str(value))
        for name, value in conn.execute(
            "SELECT input_name,digest FROM accepted_inputs WHERE consumer='pipeline' "
            "ORDER BY input_name"
        )
        if name in selected
    )


def begin_attempt(
    conn: sqlite3.Connection,
    ctx: ParseContext,
    extract_version: int | str,
    parser_version: int | str,
) -> AttemptInputs:
    """Call before extraction: requeued work and changed recipes remain visible."""
    row = conn.execute(
        "SELECT s.via,w.kind,w.source,w.parser,w.archive_url FROM snapshots s JOIN watches w USING(watch_id) WHERE snapshot_id=?",
        (ctx.snapshot_id,),
    ).fetchone()
    if row is None:
        raise ValueError("Missing source snapshot")
    unit_key = (
        f"{ctx.watch_id}/{ctx.snapshot_id}" if _independent_capture(*row[:4]) else ctx.watch_id
    )
    prior = conn.execute(
        "SELECT accepted_generation_id FROM source_units WHERE unit_key=?", (unit_key,)
    ).fetchone()
    work = conn.execute(
        "SELECT enqueued_at FROM pending_work WHERE stage='parse' AND unit_kind='snapshot' AND unit_id=?",
        (ctx.snapshot_id,),
    ).fetchone()
    return AttemptInputs(
        unit_key,
        ctx,
        parse_inputs(conn, ctx.kind),
        None if prior is None else prior[0],
        None if work is None else str(work[0]),
        str(extract_version),
        str(parser_version),
        row[4],
    )


def _independent_capture(via: str, kind: str, source: str, parser: str) -> bool:
    return (
        via == "wayback"
        and kind == "index"
        and parser in PHASE1_KINDS
        and parser.split(".")[0] == source
    )


def selected_snapshot(conn: sqlite3.Connection, watch_id: str, snapshot_id: str) -> bool:
    """Select an explicitly requested archive capture, or the latest origin body.

    Memento capture time is historical evidence, not the order in which an
    operator chose fallback captures. Only retries of the active archive URL
    compete by fetch time. Phase-one listing captures remain independent.
    """
    snapshot = conn.execute(
        "SELECT s.via,w.kind,w.source,w.parser,w.archive_url,s.body_sha256 "
        "FROM snapshots s JOIN watches w USING(watch_id) "
        "WHERE s.watch_id=? AND s.snapshot_id=?",
        (watch_id, snapshot_id),
    ).fetchone()
    if snapshot is None or snapshot[5] is None:
        return False
    if _independent_capture(*snapshot[:4]):
        return True
    if snapshot[0] == "wayback" and not snapshot[4]:
        return False
    if snapshot[4]:
        row = conn.execute(
            "SELECT snapshot_id FROM snapshots WHERE watch_id=? AND via='wayback' "
            "AND body_sha256 IS NOT NULL AND (requested_archive_url=? OR archive_url=?) "
            "ORDER BY fetched_at DESC,snapshot_id DESC LIMIT 1",
            (watch_id, snapshot[4], snapshot[4]),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT snapshot_id FROM snapshots WHERE watch_id=? AND body_sha256 IS NOT NULL "
            "ORDER BY COALESCE(observed_at,fetched_at) DESC,snapshot_id DESC LIMIT 1",
            (watch_id,),
        ).fetchone()
    return row is not None and row[0] == snapshot_id


def current_snapshot(conn: sqlite3.Connection, attempt: AttemptInputs) -> bool:
    context = attempt.context
    watch = conn.execute(
        "SELECT w.source,w.parser,w.source_ref,w.url,w.archive_url,w.kind,s.via "
        "FROM watches w JOIN snapshots s USING(watch_id) WHERE w.watch_id=? AND s.snapshot_id=?",
        (context.watch_id, context.snapshot_id),
    ).fetchone()
    if watch is None or tuple(watch[:4]) != (
        context.source,
        context.kind,
        context.source_ref,
        context.url,
    ):
        return False
    independent = _independent_capture(watch[6], watch[5], watch[0], watch[1])
    expected_unit = f"{context.watch_id}/{context.snapshot_id}" if independent else context.watch_id
    if attempt.unit_key != expected_unit:
        return False
    if not independent and watch[4] != attempt.archive_url:
        return False
    return selected_snapshot(conn, context.watch_id, context.snapshot_id)


@dataclass(frozen=True)
class Staged:
    """A staged generation's id and, when a parse repeated a blocked
    generation exactly (D-0212), that generation's state: admission would
    decide the same, so the caller reports it without admitting again."""

    generation_id: str
    repeated: str | None = None


#: States a parse can be held in whose repeat is decided by bytes and policy alone.
BLOCKED = frozenset({"needs_review", "waiting_for_inputs"})


def stage_generation(
    conn: sqlite3.Connection,
    archive: Archive,
    attempt: AttemptInputs,
    extract_sha256: str | None,
    result: ParseResult,
    report: Report,
    *,
    now: str,
    run_id: str,
    manifest: tuple[dict[str, Any], ...] | None = None,
) -> str:
    """The caller commits staging before trying promotion, retaining failures."""
    return stage(
        conn,
        archive,
        attempt,
        extract_sha256,
        result,
        report,
        now=now,
        run_id=run_id,
        manifest=manifest,
    ).generation_id


def stage(
    conn: sqlite3.Connection,
    archive: Archive,
    attempt: AttemptInputs,
    extract_sha256: str | None,
    result: ParseResult,
    report: Report,
    *,
    now: str,
    run_id: str,
    manifest: tuple[dict[str, Any], ...] | None = None,
) -> Staged:
    """`stage_generation`, saying whether it repeated a blocked generation."""
    ctx = attempt.context
    if ctx.kind == "wdr.rounds":
        report = replace(
            report, guards=(*report.guards, wdr_preserves_rows(conn, ctx.watch_id, result))
        )
    snapshot = conn.execute(
        "SELECT * FROM snapshots WHERE snapshot_id=? AND watch_id=?",
        (ctx.snapshot_id, ctx.watch_id),
    ).fetchone()
    if snapshot is None:
        raise ValueError("Missing source snapshot")
    if manifest is None:
        manifest = (
            {
                "slot": ctx.snapshot_id,
                "snapshot_id": ctx.snapshot_id,
                "watch_id": ctx.watch_id,
                "url": ctx.url,
                "body_sha256": snapshot["body_sha256"],
                "extract_sha256": extract_sha256,
                "observed_at": ctx.fetched_at,
                "captured_at": snapshot["captured_at"],
                "archive_url": snapshot["archive_url"],
                "via": snapshot["via"],
                "extract_version": attempt.extract_version,
                "parser_version": attempt.parser_version,
            },
        )
    # Referenced bytes already exist durably; no source request is permitted.
    for item in manifest:
        try:
            archive.read_body(str(item["body_sha256"]))
            if item["extract_sha256"] is not None:
                archive.read_extract(str(item["extract_sha256"]))
            else:
                raise FileNotFoundError("No successful extract exists for this attempt")
        except FileNotFoundError:
            report = replace(
                report,
                guards=(
                    *report.guards,
                    Guard(
                        "manifest_artifact_missing",
                        False,
                        "An input artifact must be restored before admission",
                    ),
                ),
            )
        except (OSError, ValueError) as exc:
            report = replace(
                report, guards=(*report.guards, Guard("manifest_artifact_invalid", False, str(exc)))
            )
    recipe = {
        "context": asdict(ctx),
        "extract_version": attempt.extract_version,
        "parser_version": attempt.parser_version,
        "contract_version": report.contract_version,
        "accepted_inputs": attempt.accepted_inputs,
    }
    if attempt.archive_url is not None:
        recipe["archive_url"] = attempt.archive_url
    fingerprint = digest(canonical({"manifest": manifest, "recipe": recipe}))
    result_json = serialize_result(result)
    evidence = {
        "unit": attempt.unit_key,
        "fingerprint": fingerprint,
        "report": report.as_dict(),
        "result": result_json,
        "previous": attempt.previous_generation_id,
        "work_token": attempt.work_token,
    }
    identifier = "gen_" + digest(canonical(evidence))
    kept = _cutoff(
        conn,
        attempt,
        fingerprint=fingerprint,
        manifest_json=canonical(manifest).decode(),
        result_json=result_json,
        report_json=canonical(report.as_dict()).decode(),
        contract_version=report.contract_version,
    )
    if kept is not None:
        if kept[1] != fingerprint:
            conn.execute(
                "INSERT INTO source_equivalences(unit_key,input_fingerprint,generation_id,"
                "recipe_json,work_token,created_at,run_id) VALUES (?,?,?,?,?,?,?) "
                "ON CONFLICT DO NOTHING",
                (
                    attempt.unit_key,
                    fingerprint,
                    kept[0],
                    canonical(recipe).decode(),
                    attempt.work_token,
                    now,
                    run_id,
                ),
            )
        conn.execute(
            "UPDATE source_units SET desired_fingerprint=? WHERE unit_key=?",
            (fingerprint, attempt.unit_key),
        )
        return Staged(kept[0], kept[2])
    conn.execute(
        "INSERT INTO source_units(unit_key,watch_id,page_kind) VALUES (?,?,?) ON CONFLICT(unit_key) DO NOTHING",
        (attempt.unit_key, ctx.watch_id, ctx.kind),
    )
    if current_snapshot(conn, attempt) and parse_inputs(conn, ctx.kind) == attempt.accepted_inputs:
        conn.execute(
            "UPDATE source_units SET desired_fingerprint=? WHERE unit_key=?",
            (fingerprint, attempt.unit_key),
        )
    conn.execute(
        "INSERT INTO admission_policies(page_kind,contract_version,policy_revision) VALUES (?,?,?) ON CONFLICT(page_kind) DO NOTHING",
        (ctx.kind, report.contract_version, "shadow:" + report.contract_version),
    )
    conn.execute(
        "INSERT INTO source_generations(generation_id,unit_key,page_kind,contract_version,input_fingerprint,manifest_json,recipe_json,result_json,report_json,previous_generation_id,work_token,created_at,run_id,state,removal_authority) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(generation_id) DO NOTHING",
        (
            identifier,
            attempt.unit_key,
            ctx.kind,
            report.contract_version,
            fingerprint,
            canonical(manifest).decode(),
            canonical(recipe).decode(),
            result_json,
            canonical(report.as_dict()).decode(),
            attempt.previous_generation_id,
            attempt.work_token,
            now,
            run_id,
            report.state,
            report.proposed_removal,
        ),
    )
    return Staged(identifier)


def _cutoff(
    conn: sqlite3.Connection,
    attempt: AttemptInputs,
    *,
    fingerprint: str,
    manifest_json: str,
    result_json: str,
    report_json: str,
    contract_version: str,
) -> tuple[str, str, str | None] | None:
    """Return the (id, fingerprint, blocked state) of a generation this parse repeats.

    An enforced unit repeats its accepted generation, or else its newest
    generation when that is held in `needs_review` or `waiting_for_inputs`
    and admission would hold this parse in the same state: the contract is
    unreviewed (needs_review), or it is reviewed and the report's own
    failures decide (D-0212). A shadow unit, which never gains an accepted
    pointer, repeats the undecided generation named by its desired
    fingerprint. Result, report, contract and manifest must be byte-equal,
    labels, snapshot ids and times included; only the recipe's accepted
    inputs may differ. The snapshot and inputs must still be current, and a
    revoked fingerprint is never cut off: admission decides it.
    """
    if (
        not cutoff.enabled()
        or conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='source_equivalences'"
        ).fetchone()
        is None
    ):
        return None
    unit = conn.execute(
        "SELECT accepted_generation_id,desired_fingerprint FROM source_units WHERE unit_key=?",
        (attempt.unit_key,),
    ).fetchone()
    if unit is None:
        return None
    columns = (
        "g.generation_id,g.input_fingerprint,g.manifest_json,g.result_json,g.report_json,"
        "g.contract_version"
    )
    same = (manifest_json, result_json, report_json, contract_version)
    policy = conn.execute(
        "SELECT mode,contract_version,reviewed_report_digest FROM admission_policies "
        "WHERE page_kind=?",
        (attempt.context.kind,),
    ).fetchone()
    blocked: str | None = None
    kept = None
    if unit[0] is not None:
        kept = conn.execute(
            f"SELECT {columns} FROM source_generations g "
            "WHERE g.generation_id=? AND g.unit_key=? AND g.state='accepted'",
            (unit[0], attempt.unit_key),
        ).fetchone()
    elif policy is not None and policy[0] == "shadow" and unit[1] is not None:
        kept = conn.execute(
            f"SELECT {columns} FROM source_generations g WHERE g.unit_key=? "
            "AND NOT EXISTS (SELECT 1 FROM admission_decisions d "
            "WHERE d.generation_id=g.generation_id) "
            "AND (g.input_fingerprint=? OR EXISTS (SELECT 1 FROM source_equivalences e "
            "WHERE e.unit_key=g.unit_key AND e.input_fingerprint=? "
            "AND e.generation_id=g.generation_id)) "
            "ORDER BY g.created_at DESC,g.generation_id DESC LIMIT 1",
            (attempt.unit_key, unit[1], unit[1]),
        ).fetchone()
    if (kept is None or tuple(kept)[2:] != same) and policy is not None and policy[0] == "enforce":
        # Admission of a fresh generation checks the contract review before
        # the report's failures, and both are fixed here: the same bytes are
        # held in the same state. Any other hold reads live state.
        state = str(json.loads(report_json).get("state"))
        expected = (
            "needs_review"
            if policy[1] != contract_version or not policy[2]
            else state
            if state in BLOCKED
            else None
        )
        newest = conn.execute(
            f"SELECT {columns},g.state FROM source_generations g WHERE g.unit_key=? "
            "ORDER BY g.created_at DESC,g.generation_id DESC LIMIT 1",
            (attempt.unit_key,),
        ).fetchone()
        if expected is not None and newest is not None and newest[6] == expected:
            kept, blocked = newest, expected
    if kept is None or tuple(kept)[2:6] != same:
        return None
    if (
        not current_snapshot(conn, attempt)
        or parse_inputs(conn, attempt.context.kind) != attempt.accepted_inputs
    ):
        return None
    if revoked_fingerprint(conn, attempt.unit_key, fingerprint):
        return None
    return str(kept[0]), str(kept[1]), blocked


def revoked_fingerprint(conn: sqlite3.Connection, unit_key: str, fingerprint: str) -> bool:
    """Whether parse inputs with this fingerprint belong to a revoked generation.

    They do when a revoked generation was staged for them, or when a parse
    cutoff recorded them as equivalent to a generation later revoked (D-0205).
    Before schema 33 there are no equivalences.
    """
    if conn.execute(
        "SELECT 1 FROM source_generations WHERE unit_key=? AND input_fingerprint=? "
        "AND state='revoked' LIMIT 1",
        (unit_key, fingerprint),
    ).fetchone():
        return True
    try:
        return (
            conn.execute(
                "SELECT 1 FROM source_equivalences e JOIN source_generations g "
                "ON g.generation_id=e.generation_id AND g.unit_key=e.unit_key "
                "WHERE e.unit_key=? AND e.input_fingerprint=? AND g.state='revoked' LIMIT 1",
                (unit_key, fingerprint),
            ).fetchone()
            is not None
        )
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc):
            return False
        raise


def serialize_result(result: ParseResult) -> str:
    return canonical(
        {
            "observations": [
                {
                    "scope": asdict(o.scope),
                    "kind": o.kind,
                    "payload": json.loads(encode_payload(o.payload)),
                }
                for o in result.observations
            ],
            "watches": [asdict(w) for w in result.watches],
            "warnings": [asdict(w) for w in result.warnings],
            "legitimate_empty": result.legitimate_empty,
        }
    ).decode()


def deserialize_result(value: str) -> ParseResult:
    data = json.loads(value)
    return ParseResult(
        tuple(
            Observation(
                ObservationScope(**o["scope"]),
                o["kind"],
                decode_payload(o["kind"], json.dumps(o["payload"])),
            )
            for o in data["observations"]
        ),
        tuple(
            WatchSpec(
                **{**w, "form": tuple(tuple(pair) for pair in w["form"]) if w["form"] else None}
            )
            for w in data["watches"]
        ),
        tuple(ParseWarning(**w) for w in data["warnings"]),
        data["legitimate_empty"],
    )
