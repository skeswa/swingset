"""Freeze source inputs and stage immutable generations without selecting output."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, replace
from typing import Any

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


def accepted_inputs(conn: sqlite3.Connection) -> tuple[tuple[str, str, str], ...]:
    return tuple(
        (str(row[0]), str(row[1]), str(row[2]))
        for row in conn.execute(
            "SELECT consumer,input_name,digest FROM accepted_inputs ORDER BY consumer,input_name"
        )
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
        accepted_inputs(conn),
        None if prior is None else prior[0],
        None if work is None else str(work[0]),
        str(extract_version),
        str(parser_version),
        row[4],
    )


def _independent_capture(via: str, kind: str, source: str, parser: str) -> bool:
    from swingset.history.acquisition import PHASE1_KINDS

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
    conn.execute(
        "INSERT INTO source_units(unit_key,watch_id,page_kind) VALUES (?,?,?) ON CONFLICT(unit_key) DO NOTHING",
        (attempt.unit_key, ctx.watch_id, ctx.kind),
    )
    if current_snapshot(conn, attempt) and accepted_inputs(conn) == attempt.accepted_inputs:
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
    return identifier


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
