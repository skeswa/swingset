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
    preserve_history: bool = False,
) -> bool:
    """Replace current observations if this snapshot is newest.

    The caller owns the transaction and deletes parse work only after this
    returns. Historical reparses still update their attempt status.
    """
    snapshot = conn.execute(
        "SELECT COALESCE(observed_at,fetched_at),via FROM snapshots WHERE snapshot_id=? AND watch_id=?",
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
            "SELECT COALESCE(observed_at,fetched_at),snapshot_id FROM snapshots WHERE snapshot_id=?",
            (current[0],),
        ).fetchone()
    eligible = current_snapshot is None or (str(snapshot[0]), context.snapshot_id) >= (
        str(current_snapshot[0]),
        str(current_snapshot[1]),
    )
    from swingset.history.acquisition import PHASE1_KINDS

    archive_index = (
        str(snapshot[1]) == "wayback"
        and context.kind in PHASE1_KINDS
        and context.kind.split(".")[0] == context.source
        and bool(
            conn.execute(
                "SELECT 1 FROM watches WHERE watch_id=? AND kind='index'", (context.watch_id,)
            ).fetchone()
        )
    )
    if str(snapshot[1]) == "wayback" and not archive_index:
        from swingset.admission.generations import selected_snapshot

        # An explicitly selected fallback can predate the incomplete page that
        # owns current observations. The admission predicate still rejects stale
        # work for a capture the active watch no longer selects.
        eligible = selected_snapshot(conn, context.watch_id, context.snapshot_id)
    snapshot_scope = archive_index or preserve_history
    changed = False
    if eligible or archive_index:
        old_count = int(
            conn.execute(
                "SELECT count(*) FROM observations WHERE watch_id=?"
                + (" AND snapshot_id=?" if snapshot_scope else ""),
                (context.watch_id, context.snapshot_id) if snapshot_scope else (context.watch_id,),
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
                    "wsdc-history"
                    if archive_index and observation.scope.kind == "calendar"
                    else observation.scope.ref,
                    seq,
                    str(extract_version),
                    str(parser_version),
                    encode_payload(observation.payload),
                )
            )
        old_rows = [
            tuple(row)
            for row in conn.execute(
                "SELECT kind,scope_kind,scope_id,seq,payload_json FROM observations WHERE watch_id=?"
                + (" AND snapshot_id=?" if snapshot_scope else "")
                + " ORDER BY seq",
                (context.watch_id, context.snapshot_id) if snapshot_scope else (context.watch_id,),
            )
        ]
        comparable = [(row[3], row[4], row[5], row[6], row[9]) for row in new_rows]
        changed = old_rows != comparable
        if changed:
            conn.execute(
                "DELETE FROM observations WHERE watch_id=?"
                + (" AND snapshot_id=?" if snapshot_scope else ""),
                (context.watch_id, context.snapshot_id) if snapshot_scope else (context.watch_id,),
            )
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
        if eligible:
            conn.execute(
                "UPDATE watches SET current_observation_snapshot_id=?,extract_version=? WHERE watch_id=?",
                (context.snapshot_id, str(extract_version), context.watch_id),
            )
        from datetime import datetime

        from swingset.history.intent import retain_child_intent
        from swingset.history.origin_dispatch import managed

        historical_parent = (str(snapshot[1]) == "wayback" and not archive_index) or managed(
            conn, context.watch_id
        )
        for spec in result.watches:
            if historical_parent:
                retain_child_intent(conn, context, spec, now=parsed_at, run_id=run_id)
                continue
            # Phase-1 platform captures contribute event rows, but their child
            # score sheets enter only through the explicit G2/G3 backfill planner.
            if archive_index and spec.kind != "index":
                continue
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
        owner_id=context.snapshot_id if archive_index else context.watch_id,
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
