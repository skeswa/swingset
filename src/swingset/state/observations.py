"""Transactional parse-result persistence."""

from __future__ import annotations

from swingset.model.ids import observation_id
from swingset.model.observations import encode_payload
from swingset.schedule.watches import upsert_watch
from swingset.sources.base import ParseContext, ParseResult
from swingset.state import engine

from .findings import Finding, replace_findings
from .work import WorkUnit, bump_revision, enqueue


def observation_rows(
    context: ParseContext,
    result: ParseResult,
    *,
    extract_version: int | str,
    parser_version: int | str,
    archive_index: bool = False,
) -> list[tuple[object, ...]]:
    """Encode the observation cache independently of admission or scheduler effects."""
    from swingset.sources.phase_one import stored_scope_ref

    return [
        (
            observation_id(context.watch_id, context.snapshot_id, observation.kind, seq),
            context.watch_id,
            context.snapshot_id,
            observation.kind,
            observation.scope.kind,
            stored_scope_ref(archive_index, observation.scope.kind, observation.scope.ref),
            seq,
            str(extract_version),
            str(parser_version),
            encode_payload(observation.payload),
        )
        for seq, observation in enumerate(result.observations)
    ]


def store_parse_result(
    conn: engine.Connection,
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
        "SELECT COALESCE(observed_at, fetched_at) AS _value_0, via FROM snapshots WHERE snapshot_id = %s AND watch_id = %s",
        (context.snapshot_id, context.watch_id),
    ).fetchone()
    if snapshot is None:
        raise ValueError("parse context does not identify an archived snapshot")
    current = conn.execute(
        "SELECT current_observation_snapshot_id FROM watches WHERE watch_id = %s",
        (context.watch_id,),
    ).fetchone()
    if current is None:
        raise ValueError(f"unknown watch: {context.watch_id}")
    current_snapshot = None
    if current[0] is not None:
        current_snapshot = conn.execute(
            "SELECT COALESCE(observed_at, fetched_at) AS _value_0, snapshot_id FROM snapshots WHERE snapshot_id = %s",
            (current[0],),
        ).fetchone()
    eligible = current_snapshot is None or (str(snapshot[0]), context.snapshot_id) >= (
        str(current_snapshot[0]),
        str(current_snapshot[1]),
    )
    from swingset.sources.phase_one import PHASE1_KINDS

    index_watch = (
        context.kind in PHASE1_KINDS
        and context.kind.split(".")[0] == context.source
        and bool(
            conn.execute(
                "SELECT 1 AS _value_0 FROM watches WHERE watch_id = %s AND kind = 'index'",
                (context.watch_id,),
            ).fetchone()
        )
    )
    archive_index = str(snapshot[1]) == "wayback" and index_watch
    if str(snapshot[1]) == "wayback" and not archive_index:
        from swingset.admission.generations import selected_snapshot

        # An explicitly selected fallback can predate the incomplete page that
        # owns current observations. The admission predicate still rejects stale
        # work for a capture the active watch no longer selects.
        eligible = selected_snapshot(conn, context.watch_id, context.snapshot_id)
    snapshot_scope = archive_index or preserve_history
    # The rows a parse replaces: its own snapshot's, or the watch's. A live
    # capture of a listing watch never replaces an archived capture read on
    # the same watch: that capture is its own unit (D-0257).
    owned = " AND snapshot_id=%s" if snapshot_scope else ""
    owned_args: tuple[str, ...] = (
        (context.watch_id, context.snapshot_id) if snapshot_scope else (context.watch_id,)
    )
    if not snapshot_scope and index_watch:
        owned = " AND snapshot_id NOT IN (SELECT snapshot_id FROM snapshots WHERE watch_id=%s AND via='wayback')"
        owned_args = (context.watch_id, context.watch_id)
    changed = False
    if eligible or archive_index:
        old_count = int(
            conn.execute(
                "SELECT COUNT(*) AS _value_0 FROM observations WHERE watch_id = %s" + owned,
                owned_args,
            ).fetchone()[0]
        )
        if not result.observations and old_count and not legitimate_empty:
            raise ValueError("unexplained empty parse output would erase current observations")
        old_scopes = {
            (str(row[0]), str(row[1]))
            for row in conn.execute(
                "SELECT DISTINCT scope_kind, scope_id FROM observations WHERE watch_id = %s",
                (context.watch_id,),
            )
        }
        new_rows = observation_rows(
            context,
            result,
            extract_version=extract_version,
            parser_version=parser_version,
            archive_index=archive_index,
        )
        old_rows = [
            tuple(row)
            for row in conn.execute(
                (
                    "SELECT kind, scope_kind, scope_id, seq, payload_json, snapshot_id, CAST(extract_version AS TEXT) AS _value_6, CAST(parser_version AS TEXT) AS _value_7 FROM observations WHERE watch_id = %s"
                )
                + owned
                + " ORDER BY seq",
                owned_args,
            )
        ]
        comparable = [
            (row[3], row[4], row[5], row[6], row[9], row[2], row[7], row[8]) for row in new_rows
        ]
        changed = old_rows != comparable
        if changed:
            conn.execute("DELETE FROM observations WHERE watch_id=%s" + owned, owned_args)
            conn.executemany(
                (
                    "INSERT INTO observations (observation_id, watch_id, snapshot_id, kind, scope_kind, "
                    "scope_id, seq, extract_version, parser_version, payload_json) VALUES (%s, %s, %s, %s, %s, "
                    "%s, %s, %s, %s, %s)"
                ),
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
                "UPDATE watches SET current_observation_snapshot_id = %s, extract_version = %s WHERE watch_id = %s",
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
    mark_parsed(
        conn,
        context.snapshot_id,
        extract_version=extract_version,
        parser_version=parser_version,
        parsed_at=parsed_at,
    )
    return changed


def mark_parsed(
    conn: engine.Connection,
    snapshot_id: str,
    *,
    extract_version: int | str,
    parser_version: int | str,
    parsed_at: str,
) -> None:
    """Record a successful parse on the snapshot row.

    `store_parse_result` does this as part of admission. A parse cut off to
    the unit's accepted generation skips admission, so the caller records it
    here: a failed attempt in between (a parser regression, then its fix)
    would otherwise leave the snapshot `failed` (D-0205).
    """
    conn.execute(
        (
            "UPDATE snapshots SET parse_status = 'ok', parsed_at = %s, extract_version = %s, "
            "parser_version = %s WHERE snapshot_id = %s"
        ),
        (parsed_at, str(extract_version), str(parser_version), snapshot_id),
    )


def load_scope(conn: engine.Connection, scope_kind: str, scope_id: str) -> tuple[object, ...]:
    """Decode current source evidence at the storage boundary."""
    from swingset.model.observations import decode_payload

    return tuple(
        decode_payload(str(row[0]), str(row[1]))
        for row in conn.execute(
            (
                "SELECT kind, payload_json FROM observations WHERE scope_kind = %s AND scope_id = %s ORDER "
                "BY watch_id NULLS FIRST, seq NULLS FIRST"
            ),
            (scope_kind, scope_id),
        )
    )
