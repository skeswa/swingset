"""Transactional parse-result persistence."""

from __future__ import annotations

import sqlite3

from swingset.model.ids import observation_id
from swingset.model.observations import encode_payload
from swingset.schedule.watches import upsert_watch
from swingset.sources.base import ParseContext, ParseResult

from .findings import Finding, replace_findings
from .work import WorkUnit, bump_revision, enqueue


def store_parse_result(
    conn: sqlite3.Connection,
    context: ParseContext,
    result: ParseResult,
    *,
    extract_version: int | str,
    parser_version: int | str,
    parsed_at: str,
    run_id: str,
    legitimate_empty: bool = False,
) -> bool:
    """Replace current observations if this snapshot is newest.

    The caller owns the transaction and deletes parse work only after this
    returns. Historical reparses still update their attempt status.
    """
    snapshot = conn.execute(
        "SELECT fetched_at FROM snapshots WHERE snapshot_id=? AND watch_id=?",
        (context.snapshot_id, context.watch_id),
    ).fetchone()
    if snapshot is None:
        raise ValueError("parse context does not identify an archived snapshot")
    current = conn.execute(
        "SELECT current_observation_snapshot_id FROM watches WHERE watch_id=?", (context.watch_id,)
    ).fetchone()
    if current is None:
        raise ValueError(f"unknown watch: {context.watch_id}")
    current_snapshot = None
    if current[0] is not None:
        current_snapshot = conn.execute(
            "SELECT fetched_at,snapshot_id FROM snapshots WHERE snapshot_id=?", (current[0],)
        ).fetchone()
    eligible = current_snapshot is None or (str(snapshot[0]), context.snapshot_id) >= (
        str(current_snapshot[0]),
        str(current_snapshot[1]),
    )
    changed = False
    if eligible:
        old_count = int(
            conn.execute(
                "SELECT count(*) FROM observations WHERE watch_id=?", (context.watch_id,)
            ).fetchone()[0]
        )
        if not result.observations and old_count and not legitimate_empty:
            raise ValueError("unexplained empty parse output would erase current observations")
        old_scopes = {
            (str(row[0]), str(row[1]))
            for row in conn.execute(
                "SELECT DISTINCT scope_kind,scope_id FROM observations WHERE watch_id=?",
                (context.watch_id,),
            )
        }
        new_rows: list[tuple[object, ...]] = []
        for seq, observation in enumerate(result.observations):
            new_rows.append(
                (
                    observation_id(context.watch_id, context.snapshot_id, observation.kind, seq),
                    context.watch_id,
                    context.snapshot_id,
                    observation.kind,
                    observation.scope.kind,
                    observation.scope.ref,
                    seq,
                    str(extract_version),
                    str(parser_version),
                    encode_payload(observation.payload),
                )
            )
        old_rows = [
            tuple(row)
            for row in conn.execute(
                "SELECT kind,scope_kind,scope_id,seq,payload_json FROM observations WHERE watch_id=? ORDER BY seq",
                (context.watch_id,),
            )
        ]
        comparable = [(row[3], row[4], row[5], row[6], row[9]) for row in new_rows]
        changed = old_rows != comparable
        if changed:
            conn.execute("DELETE FROM observations WHERE watch_id=?", (context.watch_id,))
            conn.executemany(
                "INSERT INTO observations(observation_id,watch_id,snapshot_id,kind,scope_kind,scope_id,seq,extract_version,parser_version,payload_json) VALUES (?,?,?,?,?,?,?,?,?,?)",
                new_rows,
            )
            new_scopes = {(str(row[4]), str(row[5])) for row in new_rows}
            units = [
                WorkUnit("project", scope_kind, scope_id)
                for scope_kind, scope_id in old_scopes | new_scopes
            ]
            if any(kind in {"calendar", "source_index"} for kind, _ in old_scopes | new_scopes):
                units.append(WorkUnit("project", "map", "all"))
            enqueue(conn, units, enqueued_at=parsed_at)
            bump_revision(conn, "observations")
        conn.execute(
            "UPDATE watches SET current_observation_snapshot_id=?,extract_version=? WHERE watch_id=?",
            (context.snapshot_id, str(extract_version), context.watch_id),
        )
        from datetime import datetime

        for spec in result.watches:
            upsert_watch(
                conn,
                spec,
                datetime.fromisoformat(parsed_at.replace("Z", "+00:00")),
                snapshot_id=context.snapshot_id,
                parent_watch_id=context.watch_id,
            )
    warning_findings = tuple(
        Finding(
            kind="parse_warning",
            subject_kind="watch",
            subject_id=context.watch_id,
            severity="warning",
            summary=warning.message,
            evidence={"code": warning.code, "evidence": warning.evidence},
            watch_id=context.watch_id,
            snapshot_id=context.snapshot_id,
        )
        for warning in result.warnings
    )
    replace_findings(
        conn,
        owner_kind="parse",
        owner_id=context.watch_id,
        findings=warning_findings,
        opened_at=parsed_at,
        run_id=run_id,
    )
    conn.execute(
        "UPDATE snapshots SET parse_status='ok',parsed_at=?,extract_version=?,parser_version=? WHERE snapshot_id=?",
        (parsed_at, str(extract_version), str(parser_version), context.snapshot_id),
    )
    return changed


def load_scope(conn: sqlite3.Connection, scope_kind: str, scope_id: str) -> tuple[object, ...]:
    """Decode current source evidence at the storage boundary."""
    from swingset.model.observations import decode_payload

    return tuple(
        decode_payload(str(row[0]), str(row[1]))
        for row in conn.execute(
            "SELECT kind,payload_json FROM observations WHERE scope_kind=? AND scope_id=? ORDER BY watch_id,seq",
            (scope_kind, scope_id),
        )
    )
