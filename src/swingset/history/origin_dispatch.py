"""Durable, gated WP16 origin intent and actual-request cadence accounting."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from datetime import UTC, date, datetime
from urllib.parse import urlsplit

from swingset.admission.generations import deserialize_result
from swingset.admission.support import interpretation_support
from swingset.clock import Clock
from swingset.config import Config
from swingset.fetch.wayback import Capture
from swingset.history.acquisition import phase_two_gate
from swingset.history.captures import capture_state, snapshot_state
from swingset.history.origin import ArchiveSearch, OriginProposal, _same_resource, propose_origin
from swingset.history.platform import KnownEvent, Page, PlannedPage, PlatformPlan
from swingset.schedule.watches import upsert_watch
from swingset.sources.base import WatchSpec
from swingset.state.control_scopes import for_watch
from swingset.state.controls import matching_pauses
from swingset.state.db import Database


def available(conn: sqlite3.Connection) -> bool:
    return (
        conn.execute("SELECT 1 FROM sqlite_master WHERE name='history_origin_intents'").fetchone()
        is not None
    )


def managed(conn: sqlite3.Connection, watch_id: str) -> bool:
    return (
        available(conn)
        and conn.execute(
            "SELECT 1 FROM history_origin_intents WHERE watch_id=?", (watch_id,)
        ).fetchone()
        is not None
    )


def cadence_reason(
    conn: sqlite3.Connection, *, source: str, event_id: str, run_id: str, now: datetime
) -> str | None:
    if conn.execute(
        "SELECT 1 FROM history_origin_requests WHERE run_id=? AND source=? AND event_id!=? LIMIT 1",
        (run_id, source, event_id),
    ).fetchone():
        return "origin_event_cycle_limit"
    if (
        source == "dcn"
        and conn.execute(
            "SELECT 1 FROM history_origin_requests WHERE source='dcn' AND day=? AND event_id!=? LIMIT 1",
            (now.astimezone(UTC).date().isoformat(), event_id),
        ).fetchone()
    ):
        return "origin_event_daily_limit"
    return None


def retained_proposal(
    conn: sqlite3.Connection, item: PlannedPage, *, now: datetime, history_start: date
) -> OriginProposal:
    # Read all retained captures, not only the planner's preferred three: a usable
    # older interpretation still prevents an origin request.
    held = tuple(
        Capture(str(row[0]), str(row[1]), str(row[2]), str(row[3]), int(row[4]), int(row[5]))
        for row in conn.execute(
            "SELECT url,timestamp,digest,mimetype,length,status FROM archive_captures WHERE source=?",
            (item.page.source,),
        )
        if _same_resource(str(row[0]), item.page.url)
    )
    outcomes = {
        capture.archive_url: capture_state(
            conn, replace(item, page=replace(item.page, url=capture.url)), capture, now=now
        )
        for capture in held
    }
    queries = tuple(
        ArchiveSearch(*tuple(row))
        for row in conn.execute(
            "SELECT query_id,source,prefix,year,next_page,total_pages,completed_at FROM archive_queries WHERE source=?",
            (item.page.source,),
        )
    )
    operator = (
        frozenset(
            str(row[0])
            for row in conn.execute(
                "SELECT source_ref FROM history_origin_operator_refs WHERE length(trim(evidence_reference))>0"
            )
        )
        if available(conn)
        else frozenset()
    )
    return propose_origin(
        item.event,
        item.page,
        captures=held,
        outcomes=outcomes,
        searches=queries,
        now=now,
        gate_reason=phase_two_gate(
            conn,
            source=item.page.source,
            source_ref=item.page.source_ref,
            page_kind=item.page.page_kind,
        ),
        operator_pre2018_refs=operator,
        history_start=history_start,
    )


def _known(
    conn: sqlite3.Connection, source: str, source_ref: str, page: Page
) -> PlannedPage | None:
    row = conn.execute(
        "SELECT e.event_id,e.year,e.event_month,e.end_date FROM source_event_map m JOIN events e USING(event_id) WHERE m.source=? AND m.source_ref=?",
        (source, source_ref),
    ).fetchone()
    if row is None:
        return None
    return PlannedPage(
        KnownEvent(
            str(row[0]),
            source,
            source_ref,
            int(row[1]),
            str(row[2]),
            date.fromisoformat(row[3]) if row[3] else None,
        ),
        page,
        (),
    )


def _parent_admitted(conn: sqlite3.Connection, page: Page) -> bool:
    """A round debit needs a current, admitted exact parent declaration.

    Partial parents may declare useful children. Their other missing rounds do
    not invalidate that declaration, but revoked or stale interpretations do.
    """
    if page.kind != "round":
        return True
    parents = conn.execute(
        "SELECT g.result_json,g.recipe_json,g.page_kind,w.url AS parent_url "
        "FROM source_units u JOIN source_generations g ON g.generation_id=u.accepted_generation_id "
        "JOIN watches w ON w.watch_id=u.watch_id "
        "WHERE w.source=? AND w.source_ref=? AND w.kind!='round' AND g.state='accepted' "
        "AND w.parser=g.page_kind "
        "AND (? IS NULL OR w.url=?)",
        (page.source, page.source_ref, page.parent_url, page.parent_url),
    )
    for parent in parents:
        recipe = json.loads(parent["recipe_json"])
        context = recipe["context"]
        if (context["source"], context["source_ref"], context["url"]) != (
            page.source,
            page.source_ref,
            parent["parent_url"],
        ):
            continue
        if phase_two_gate(
            conn, source=page.source, source_ref=page.source_ref, page_kind=parent["page_kind"]
        ):
            continue
        support = interpretation_support(
            conn,
            recipe["context"]["snapshot_id"],
            parser_version=str(recipe["parser_version"]),
            extract_version=str(recipe["extract_version"]),
        )
        if not support["usable"]:
            continue
        if any(
            child.source == page.source
            and child.source_ref == page.source_ref
            and child.url == page.url
            and child.parser == page.page_kind
            and child.kind == page.kind
            and child.method == "GET"
            and not child.form
            for child in deserialize_result(parent["result_json"]).watches
        ):
            return True
    return False


def candidate(
    database: Database,
    config: Config,
    clock: Clock,
    item: PlannedPage,
    plan: PlatformPlan,
    *,
    run_id: str | None,
) -> tuple[WatchSpec | None, OriginProposal | None, str]:
    from swingset.history.backfill import _parent_ready

    conn, page = database.connection, item.page
    if not available(conn):
        return None, None, "origin_schema_unavailable"
    if not config.enabled(page.source):
        return None, None, "source_disabled"
    spec = WatchSpec(
        "", page.source, page.kind, "GET", page.url, page.page_kind, source_ref=page.source_ref
    )
    actual = _known(conn, page.source, page.source_ref, page)
    if actual is None or actual.event != item.event:
        return None, None, "plan_mapping_changed"
    if run_id and (
        reason := cadence_reason(
            conn, source=page.source, event_id=item.event.event_id, run_id=run_id, now=clock.now()
        )
    ):
        return None, None, reason
    if matching_pauses(
        conn,
        for_watch(
            conn,
            source=page.source,
            watch_id=spec.watch_id,
            page_kind=page.page_kind,
            watch_kind=page.kind,
            host=urlsplit(page.url).hostname,
        ),
        now=clock.now(),
    ):
        return None, None, "operator_pause"
    existing = conn.execute("SELECT * FROM watches WHERE watch_id=?", (spec.watch_id,)).fetchone()
    ours = managed(conn, spec.watch_id)
    if existing:
        if not existing["archive_url"] and not ours:
            return None, None, "independent_origin_control"
        if (
            existing["state"] in {"gone", "paused", "retired"}
            or existing["paused_until"]
            and datetime.fromisoformat(existing["paused_until"]) > clock.now()
        ):
            return None, None, "watch_cooldown"
        if ours:
            snapshot = conn.execute(
                "SELECT * FROM snapshots WHERE watch_id=? AND via='origin' AND http_status=200 AND classification='Ok' ORDER BY fetched_at DESC,snapshot_id DESC LIMIT 1",
                (spec.watch_id,),
            ).fetchone()
            if snapshot and snapshot_state(conn, item, snapshot, now=clock.now()).state in {
                "complete",
                "waiting",
            }:
                return None, None, "origin_interpretation_retained"
            if (
                existing["next_check_at"]
                and datetime.fromisoformat(existing["next_check_at"]) > clock.now()
            ):
                return None, None, "watch_cooldown"
    proposal = retained_proposal(conn, item, now=clock.now(), history_start=config.history_start)
    if not proposal.eligible:
        return None, proposal, proposal.reason
    if not _parent_ready(database, item, plan, now=clock.now()) or not _parent_admitted(conn, page):
        return None, proposal, "parent_document_pending"
    return spec, proposal, "eligible_origin"


def schedule(
    conn: sqlite3.Connection,
    spec: WatchSpec,
    proposal: OriginProposal,
    *,
    run_id: str,
    now: datetime,
) -> None:
    if not conn.in_transaction or not proposal.eligible:
        raise ValueError("origin planning requires a gated write transaction")
    reason = phase_two_gate(
        conn, source=spec.source, source_ref=spec.source_ref, page_kind=spec.parser
    )
    if reason:
        raise ValueError(reason)
    if not _parent_admitted(conn, proposal.page):
        raise ValueError("parent_document_pending")
    upsert_watch(conn, spec, now)
    evidence = json.dumps(
        {
            "reason": proposal.reason,
            "archive_query_ids": proposal.archive_query_ids,
            "attempted_capture_urls": proposal.attempted_capture_urls,
        },
        sort_keys=True,
    )
    conn.execute(
        "INSERT INTO history_origin_intents VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(watch_id) DO UPDATE SET source=excluded.source,source_ref=excluded.source_ref,event_id=excluded.event_id,page_kind=excluded.page_kind,url=excluded.url,parent_url=excluded.parent_url,evidence_json=excluded.evidence_json,dispatch_run_id=excluded.dispatch_run_id",
        (
            spec.watch_id,
            spec.source,
            spec.source_ref,
            proposal.event.event_id,
            spec.parser,
            spec.url,
            proposal.page.parent_url,
            evidence,
            now.isoformat(),
            run_id,
        ),
    )
    conn.execute(
        "UPDATE watches SET archive_url=NULL,state='backfill',priority=6,next_check_at=? WHERE watch_id=?",
        (now.isoformat(), spec.watch_id),
    )


def request_gate(
    conn: sqlite3.Connection,
    watch: object,
    *,
    now: datetime,
    history_start: date,
    request_url: str | None = None,
    actual_host: str | None = None,
) -> str | None:
    identifier = str(getattr(watch, "watch_id", ""))
    if not managed(conn, identifier):
        return None
    intent = conn.execute(
        "SELECT * FROM history_origin_intents WHERE watch_id=?", (identifier,)
    ).fetchone()
    run_id = getattr(watch, "_origin_run_id", None)
    if not run_id or intent["dispatch_run_id"] != run_id:
        return "origin_dispatch_required"
    if any(
        str(getattr(watch, attr, "")) != intent[column]
        for attr, column in (
            ("source", "source"),
            ("source_ref", "source_ref"),
            ("parser", "page_kind"),
            ("url", "url"),
        )
    ) or getattr(watch, "archive_url", None):
        return "origin_watch_binding_changed"
    if actual_host is not None and actual_host != urlsplit(intent["url"]).hostname:
        return "origin_host_unproven"
    if request_url is not None and not _same_resource(request_url, intent["url"]):
        # robots is fetched through the same gate; its resource is the sole
        # permitted auxiliary read and uses the actual origin host's controls.
        try:
            parsed = urlsplit(request_url)
            port = parsed.port
        except ValueError:
            return "origin_redirect_unproven"
        if (
            parsed.scheme not in {"http", "https"}
            or parsed.username
            or parsed.password
            or parsed.fragment
            or port not in {None, 80 if parsed.scheme == "http" else 443}
            or parsed.path != "/robots.txt"
            or parsed.hostname != urlsplit(intent["url"]).hostname
            or parsed.query
        ):
            return "origin_redirect_unproven"
    page = Page(
        intent["source"],
        intent["source_ref"],
        intent["url"],
        intent["page_kind"],
        str(getattr(watch, "kind", "")),
        intent["parent_url"],
    )
    item = _known(conn, intent["source"], intent["source_ref"], page)
    if item is None or item.event.event_id != intent["event_id"]:
        return "plan_mapping_changed"
    if not _parent_admitted(conn, page):
        return "parent_document_pending"
    proposal = retained_proposal(conn, item, now=now, history_start=history_start)
    if not proposal.eligible:
        return proposal.reason
    return cadence_reason(
        conn, source=intent["source"], event_id=intent["event_id"], run_id=run_id, now=now
    )


def record_request(
    conn: sqlite3.Connection, watch: object, *, action_id: str, now: datetime
) -> None:
    identifier = str(getattr(watch, "watch_id", ""))
    if not managed(conn, identifier):
        return
    if not conn.in_transaction:
        raise ValueError("origin cadence must share the actual HTTP debit transaction")
    intent = conn.execute(
        "SELECT * FROM history_origin_intents WHERE watch_id=?", (identifier,)
    ).fetchone()
    conn.execute(
        "INSERT INTO history_origin_requests VALUES (?,?,?,?,?,?,?)",
        (
            action_id,
            identifier,
            intent["source"],
            intent["event_id"],
            getattr(watch, "_origin_run_id", None),
            now.astimezone(UTC).date().isoformat(),
            now.isoformat(),
        ),
    )


def record_operator_reference(
    conn: sqlite3.Connection,
    *,
    source_ref: str,
    evidence_reference: str,
    recorded_at: datetime,
) -> None:
    """Record an actual operator-provided EEPro slug; never derive or discover one.

    The coordinator supplies the retained conversation reference. This receipt
    grants neither year acceptance nor parser admission and creates no control.
    """
    if not conn.in_transaction:
        raise ValueError("operator provenance requires a write transaction")
    if recorded_at.tzinfo is None or not evidence_reference.strip():
        raise ValueError("operator provenance requires an aware clock and retained reference")
    if (
        not source_ref.startswith("eepro:")
        or not conn.execute(
            "SELECT 1 FROM source_event_map WHERE source='eepro' AND source_ref=?",
            (source_ref,),
        ).fetchone()
    ):
        raise ValueError("operator slug requires a known event mapping")
    conn.execute(
        "INSERT INTO history_origin_operator_refs VALUES (?,?,?) "
        "ON CONFLICT(source_ref) DO UPDATE SET evidence_reference=excluded.evidence_reference,recorded_at=excluded.recorded_at",
        (source_ref, evidence_reference.strip(), recorded_at.isoformat()),
    )
