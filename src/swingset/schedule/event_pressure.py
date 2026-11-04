"""Conservative local-work observations gate only entry into additional events."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any
from urllib.parse import urlsplit

from swingset.config import Config
from swingset.state import engine
from swingset.state.db import Database

from .event_enumerations import memberships
from .event_request_kind import SOURCE_EVENT_PARSERS, is_source_event_request, purpose


def available(conn: engine.Connection) -> bool:
    return (
        conn.execute(
            "SELECT 1 AS _value_0 FROM pg_catalog.pg_class WHERE relname = 'event_pressure_state' AND (PG_TABLE_IS_VISIBLE(oid) AND NOT relnamespace IN (CAST('pg_catalog' AS REGNAMESPACE), CAST('information_schema' AS REGNAMESPACE)))"
        ).fetchone()
        is not None
    )


def _enabled(config: Config) -> bool:
    """`[stages] event_pressure = false` turns the whole gate off, failing open.

    Nothing refreshes observations or drains dirty rows then, so stale subjects
    or pending metadata would otherwise close `event_index` expansion for good.
    """
    return config.stages.event_pressure


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def policy(config: Config) -> dict[str, Any]:
    from .event_pressure_probe import FORMAT, Limits

    value = {
        "format": "event-pressure-v1",
        "probe_format": FORMAT,
        "high": config.scheduler.event_pressure_high,
        "low": config.scheduler.event_pressure_low,
        "max_age_seconds": config.scheduler.event_pressure_max_age_seconds,
        "refresh_events": config.scheduler.event_pressure_refresh_events,
        "probe_limits": asdict(Limits(members=config.scheduler.event_pressure_probe_pages)),
        "acceptance": "proposed_unmeasured",
    }
    return {"digest": sha256(_json(value).encode()).hexdigest(), "values": value}


def _identities(conn: engine.Connection, watch: engine.Row) -> set[tuple[str, str]]:
    return _watch_identities(
        watch, memberships(conn, [watch["watch_id"]]).get(watch["watch_id"], [])
    )


def _watch_identities(watch: engine.Row, members: list[dict[str, Any]]) -> set[tuple[str, str]]:
    result = {(row["source"], row["source_ref"]) for row in members}
    if is_source_event_request(
        source=watch["source"],
        parser=watch["parser"],
        watch_kind=watch["kind"],
        source_ref=watch["source_ref"],
    ):
        result.add((watch["source"], watch["source_ref"]))
    return result


def _start_subject(
    conn: engine.Connection, identity: tuple[str, str], *, now: datetime, basis: str
) -> None:
    conn.execute(
        (
            "INSERT INTO event_pressure_subjects (source, source_ref, enrolled_at, start_basis) VALUES "
            "(%s, %s, %s, %s) ON CONFLICT DO NOTHING"
        ),
        (*identity, now.isoformat(), basis),
    )


def _start(
    conn: engine.Connection,
    identity: tuple[str, str],
    *,
    now: datetime,
    basis: str,
    host: str | None,
    watch_id: str,
) -> None:
    _start_subject(conn, identity, now=now, basis=basis)
    conn.execute(
        (
            "INSERT INTO event_pressure_watches (watch_id, source, source_ref) VALUES (%s, %s, %s) ON "
            "CONFLICT DO NOTHING"
        ),
        (watch_id, *identity),
    )
    if host:
        conn.execute(
            "INSERT INTO event_pressure_hosts (host, source, source_ref) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
            (host, *identity),
        )


def record_started(
    conn: engine.Connection, config: Config, *, watch_id: str | None, host: str, now: datetime
) -> None:
    """Join the actual request debit transaction, including robots and redirects."""
    if not _enabled(config) or not available(conn) or watch_id is None:
        return
    if not conn.in_transaction:
        raise RuntimeError("event pressure starts require the request debit transaction")
    watch = conn.execute(
        (
            "SELECT watches.watch_id, watches.source, watches.kind, watches.method, watches.url, "
            "watches.form, watches.archive_url, watches.parser, watches.source_ref, watches.state, "
            "watches.next_check_at, watches.last_checked_at, watches.last_changed_at, "
            "watches.unchanged_streak, watches.etag, watches.last_modified, watches.body_sha256, "
            "watches.paused_until, watches.notes, watches.fingerprint, watches.extract_version, "
            "watches.priority, watches.created_by_snapshot_id, watches.parent_watch_id, watches.ever_ok, "
            "watches.current_observation_snapshot_id, watches.consecutive_404s, watches.first_404_at "
            "FROM watches WHERE watch_id = %s"
        ),
        (watch_id,),
    ).fetchone()
    if watch is None or purpose(watch["parser"]) not in {"result", "event_index"}:
        return
    for identity in _identities(conn, watch):
        _start(conn, identity, now=now, basis="issued_request", host=host, watch_id=watch_id)
    persist_latches(conn, config, now=now)


def _pending(conn: engine.Connection) -> bool:
    """Unfetched indexes alone never hold admission while metadata catches up."""
    results = _json(
        sorted(parser for parser in SOURCE_EVENT_PARSERS if purpose(parser) == "result")
    )
    return (
        conn.execute(
            (
                "SELECT 1 AS _value_0 FROM event_pressure_dirty AS d LEFT JOIN watches AS w USING (watch_id) WHERE EXISTS(SELECT 1 AS _value_0 FROM event_pressure_watches AS p WHERE p.watch_id = d.watch_id) OR (w.parser IN (SELECT value FROM jsonb_array_elements_text(%s::jsonb)) AND (EXISTS(SELECT 1 AS _value_0 FROM snapshots AS s WHERE s.watch_id = d.watch_id) OR (w.parser IN (SELECT value FROM jsonb_array_elements_text(%s::jsonb)) AND EXISTS(SELECT 1 AS _value_0 FROM source_event_member_watches AS m JOIN source_event_inventory AS i USING (enumeration_id) WHERE m.watch_id = d.watch_id)))) LIMIT 1"
            ),
            (_json(sorted(SOURCE_EVENT_PARSERS)), results),
        ).fetchone()
        is not None
    )


_INVENTORY_CURSOR = "event_pressure_inventory_after"


def _sync_inventory(conn: engine.Connection, *, now: datetime, limit: int) -> tuple[int, bool]:
    """Re-enroll retained subjects even when their last watch moved or vanished."""
    saved = conn.execute("SELECT value FROM cursors WHERE name=%s", (_INVENTORY_CURSOR,)).fetchone()
    after = int(saved[0]) if saved else 0
    page = conn.execute(
        """SELECT i.rowid,i.source,i.source_ref,
                  (EXISTS(SELECT 1 FROM source_event_enumerations e
                          JOIN source_event_member_watches m USING(enumeration_id)
                          JOIN watches w ON w.watch_id=m.watch_id
                          WHERE e.source=i.source AND e.source_ref=i.source_ref
                            AND w.parser IN (SELECT value FROM jsonb_array_elements_text(%s::jsonb)))
                   OR EXISTS(SELECT 1 FROM source_event_retirement_receipts r
                             WHERE r.source=i.source AND r.source_ref=i.source_ref)) AS historically_started
           FROM source_event_inventory i WHERE i.rowid>%s ORDER BY i.rowid LIMIT %s""",
        (
            _json(sorted(parser for parser in SOURCE_EVENT_PARSERS if purpose(parser) == "result")),
            after,
            limit + 1,
        ),
    ).fetchall()
    for row in page[:limit]:
        if row["historically_started"]:
            _start_subject(
                conn, (row["source"], row["source_ref"]), now=now, basis="retained_catalog"
            )
    if page:
        conn.execute(
            "INSERT INTO cursors(name,value) VALUES(%s,%s) ON CONFLICT(name) DO UPDATE SET value=excluded.value",
            (_INVENTORY_CURSOR, str(page[min(limit, len(page)) - 1]["rowid"])),
        )
    return min(limit, len(page)), len(page) > limit


def sync(
    conn: engine.Connection, config: Config, *, now: datetime, limit: int = 100
) -> dict[str, Any]:
    """Enroll bounded dirty watch metadata; verification is a separate operation."""
    if not available(conn):
        return {"supported": False, "processed": 0}
    if not _enabled(config):
        return {"supported": True, "disabled": True, "processed": 0}
    if not conn.in_transaction:
        raise RuntimeError("pressure metadata synchronization requires a transaction")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("pressure metadata limit must be between 1 and 100")
    dirty = list(
        conn.execute(
            "SELECT watch_id FROM event_pressure_dirty ORDER BY rowid NULLS FIRST LIMIT %s",
            (limit,),
        )
    )
    for (identifier,) in dirty:
        watch = conn.execute(
            (
                "SELECT watches.watch_id, watches.source, watches.kind, watches.method, watches.url, "
                "watches.form, watches.archive_url, watches.parser, watches.source_ref, watches.state, "
                "watches.next_check_at, watches.last_checked_at, watches.last_changed_at, "
                "watches.unchanged_streak, watches.etag, watches.last_modified, watches.body_sha256, "
                "watches.paused_until, watches.notes, watches.fingerprint, watches.extract_version, "
                "watches.priority, watches.created_by_snapshot_id, watches.parent_watch_id, watches.ever_ok, "
                "watches.current_observation_snapshot_id, watches.consecutive_404s, watches.first_404_at "
                "FROM watches WHERE watch_id = %s"
            ),
            (identifier,),
        ).fetchone()
        if watch is not None:
            kind = purpose(watch["parser"])
            declared = memberships(conn, [identifier]).get(identifier, [])
            retained = (
                conn.execute(
                    "SELECT 1 AS _value_0 FROM snapshots WHERE watch_id = %s LIMIT 1", (identifier,)
                ).fetchone()
                is not None
            )
            host = urlsplit(watch["archive_url"] or watch["url"]).hostname
            for identity in _identities(conn, watch):
                started = conn.execute(
                    "SELECT 1 AS _value_0 FROM event_pressure_subjects WHERE source = %s AND source_ref = %s",
                    identity,
                ).fetchone()
                if (
                    started
                    or (kind in {"result", "event_index"} and retained)
                    or (kind == "result" and declared)
                ):
                    _start(
                        conn,
                        identity,
                        now=now,
                        basis="retained_evidence" if retained else "admitted_result_obligation",
                        host=host,
                        watch_id=identifier,
                    )
        conn.execute("DELETE FROM event_pressure_dirty WHERE watch_id = %s", (identifier,))
    catalog_processed, catalog_pending = _sync_inventory(conn, now=now, limit=limit)
    persist_latches(conn, config, now=now)
    return {
        "supported": True,
        "processed": len(dirty),
        "catalog_processed": catalog_processed,
        "metadata_pending": _pending(conn) or catalog_pending,
    }


def bootstrap(
    database: Database, config: Config, *, now: datetime, limit: int = 100
) -> dict[str, Any]:
    with database.transaction() as conn:
        return sync(conn, config, now=now, limit=limit)


def token(
    conn: engine.Connection, source: str, source_ref: str, policy_digest: str
) -> dict[str, Any]:
    row = conn.execute(
        (
            "SELECT p.revision, i.enumeration_id FROM event_pressure_subjects AS p LEFT JOIN "
            "source_event_inventory AS i USING (source, source_ref) WHERE p.source = %s AND p.source_ref "
            "= %s"
        ),
        (source, source_ref),
    ).fetchone()
    bundle = conn.execute("SELECT value FROM meta WHERE key = 'input_bundle_hash'").fetchone()
    return {
        "revision": row[0] if row else None,
        "enumeration_id": row[1] if row else None,
        "epoch": conn.execute("SELECT epoch FROM event_pressure_state").fetchone()[0],
        "input_bundle_hash": bundle[0] if bundle else None,
        "policy_digest": policy_digest,
    }


def subject_status(
    conn: engine.Connection, row: engine.Row, *, now: datetime, policy_digest: str
) -> str:
    observation = json.loads(row["observation_json"]) if row["observation_json"] else None
    if observation is None:
        return "unknown"
    if observation["token"] != token(conn, row["source"], row["source_ref"], policy_digest):
        return "changed"
    if datetime.fromisoformat(observation["valid_until"]) <= now:
        return "stale"
    if not observation["parent_valid"] or not observation["checked"] or observation["unknown"]:
        return "unknown"
    return (
        "local_gap"
        if any(
            observation[stage] != observation["checked"] for stage in ("acquired", "interpreted")
        )
        else "locally_accounted"
    )


def admission(
    conn: engine.Connection, config: Config, *, host: str, now: datetime
) -> dict[str, Any]:
    """Read compact hints only. An open expansion gate grants no fetch permission."""
    if not available(conn):
        return {"supported": False, "deferred": False, "reason": "pressure_schema_unavailable"}
    if not _enabled(config):
        return {
            "supported": True,
            "disabled": True,
            "host": host,
            "deferred": False,
            "watermark_deferred": False,
            "metadata_pending": False,
            "reason": None,
        }
    captured = policy(config)
    counts: dict[str, int] = {}
    for row in conn.execute(
        (
            "SELECT p.source, p.source_ref, p.enrolled_at, p.start_basis, p.revision, "
            "p.checked_sequence, p.scan_json, p.observation_json FROM event_pressure_hosts AS h JOIN "
            "event_pressure_subjects AS p USING (source, source_ref) WHERE h.host = %s"
        ),
        (host,),
    ):
        state = subject_status(conn, row, now=now, policy_digest=captured["digest"])
        counts[state] = counts.get(state, 0) + 1
    total = sum(counts.values())
    pressure = total - counts.get("locally_accounted", 0)
    old = conn.execute(
        "SELECT deferred FROM event_pressure_latches WHERE host = %s", (host,)
    ).fetchone()
    deferred = bool(old[0]) if old else False
    if pressure > config.scheduler.event_pressure_high:
        deferred = True
    elif pressure < config.scheduler.event_pressure_low:
        deferred = False
    pending = _pending(conn)
    return {
        "supported": True,
        "host": host,
        "deferred": deferred or pending,
        "watermark_deferred": deferred,
        "metadata_pending": pending,
        "reason": "event pressure metadata pending"
        if pending
        else "event expansion pressure"
        if deferred
        else None,
        "started_subjects": total,
        "pressure_subjects": pressure,
        "states": counts,
        "policy": captured,
        "basis": "local acquisition/interpretation scheduling hints; no completion authority",
    }


def persist_latches(conn: engine.Connection, config: Config, *, now: datetime) -> None:
    for (host,) in list(conn.execute("SELECT DISTINCT host FROM event_pressure_hosts")):
        value = admission(conn, config, host=host, now=now)
        conn.execute(
            (
                "INSERT INTO event_pressure_latches (host, deferred, policy_digest, policy_json, changed_at) VALUES (%s, %s, %s, %s, %s) ON CONFLICT(host) DO UPDATE SET deferred = excluded.deferred, policy_digest = excluded.policy_digest, policy_json = excluded.policy_json, changed_at = excluded.changed_at WHERE event_pressure_latches.deferred <> excluded.deferred OR event_pressure_latches.policy_digest <> excluded.policy_digest"
            ),
            (
                host,
                int(value["watermark_deferred"]),
                value["policy"]["digest"],
                _json(value["policy"]["values"]),
                now.isoformat(),
            ),
        )


def request_denial(
    conn: engine.Connection,
    config: Config,
    *,
    watch_id: str | None,
    host: str | None,
    now: datetime,
    host_decisions: dict[str, dict[str, Any]] | None = None,
) -> str | None:
    if not _enabled(config) or not available(conn) or watch_id is None:
        return None
    watch = conn.execute(
        (
            "SELECT watches.watch_id, watches.source, watches.kind, watches.method, watches.url, "
            "watches.form, watches.archive_url, watches.parser, watches.source_ref, watches.state, "
            "watches.next_check_at, watches.last_checked_at, watches.last_changed_at, "
            "watches.unchanged_streak, watches.etag, watches.last_modified, watches.body_sha256, "
            "watches.paused_until, watches.notes, watches.fingerprint, watches.extract_version, "
            "watches.priority, watches.created_by_snapshot_id, watches.parent_watch_id, watches.ever_ok, "
            "watches.current_observation_snapshot_id, watches.consecutive_404s, watches.first_404_at "
            "FROM watches WHERE watch_id = %s"
        ),
        (watch_id,),
    ).fetchone()
    if watch is None or purpose(watch["parser"]) != "event_index":
        return None
    identities = _identities(conn, watch)
    if not identities:
        return None
    if any(
        conn.execute(
            "SELECT 1 AS _value_0 FROM event_pressure_subjects WHERE source = %s AND source_ref = %s",
            key,
        ).fetchone()
        for key in identities
    ):
        return None
    # Metadata enrollment is bounded and may lag behind retained evidence.
    # Continuations stay continuations before their hint row is materialized.
    parsers = _json(sorted(SOURCE_EVENT_PARSERS))
    for key in identities:
        retained = conn.execute(
            (
                "SELECT 1 AS _value_0 FROM watches AS w WHERE w.source = %s AND w.source_ref = %s AND w.parser IN (SELECT value FROM jsonb_array_elements_text(%s::jsonb)) AND EXISTS(SELECT 1 AS _value_0 FROM snapshots AS s WHERE s.watch_id = w.watch_id) LIMIT 1"
            ),
            (*key, parsers),
        ).fetchone()
        listed = conn.execute(
            (
                "SELECT 1 AS _value_0 FROM source_event_inventory AS i JOIN source_event_member_watches AS m USING (enumeration_id) JOIN watches AS w USING (watch_id) WHERE i.source = %s AND i.source_ref = %s AND w.parser IN (SELECT value FROM jsonb_array_elements_text(%s::jsonb)) LIMIT 1"
            ),
            (
                *key,
                _json(
                    sorted(parser for parser in SOURCE_EVENT_PARSERS if purpose(parser) == "result")
                ),
            ),
        ).fetchone()
        if retained or listed:
            return None
    actual_host = host or urlsplit(watch["archive_url"] or watch["url"]).hostname
    if actual_host is None:
        return None
    if host_decisions is not None and actual_host in host_decisions:
        value = host_decisions[actual_host]
    else:
        value = admission(conn, config, host=actual_host, now=now)
        if host_decisions is not None:
            host_decisions[actual_host] = value
    return value["reason"] if value["deferred"] else None


def selection_denials(
    conn: engine.Connection, config: Config, watches: list[engine.Row], *, now: datetime
) -> dict[str, str]:
    """Batch hints for a pinned selection; request admission reads live state again."""
    if not _enabled(config) or not available(conn):
        return {}
    indexes = [watch for watch in watches if purpose(watch["parser"]) == "event_index"]
    if not indexes:
        return {}
    declared = memberships(conn, [watch["watch_id"] for watch in indexes])
    identities = {
        watch["watch_id"]: _watch_identities(watch, declared.get(watch["watch_id"], []))
        for watch in indexes
    }
    keys = sorted(set().union(*identities.values()))
    if not keys:
        return {}
    continuations = set(
        map(
            tuple,
            conn.execute(
                "SELECT k.source,k.source_ref FROM jsonb_to_recordset(%s::jsonb) AS k(source text,source_ref text) "
                "WHERE EXISTS(SELECT 1 FROM event_pressure_subjects p WHERE p.source=k.source AND p.source_ref=k.source_ref) "
                "OR EXISTS(SELECT 1 FROM watches w WHERE w.source=k.source AND w.source_ref=k.source_ref AND w.parser=ANY(%s::text[]) AND EXISTS(SELECT 1 FROM snapshots s WHERE s.watch_id=w.watch_id)) "
                "OR EXISTS(SELECT 1 FROM source_event_inventory i JOIN source_event_member_watches m USING(enumeration_id) JOIN watches w USING(watch_id) WHERE i.source=k.source AND i.source_ref=k.source_ref AND w.parser=ANY(%s::text[]))",
                (
                    _json([dict(source=source, source_ref=ref) for source, ref in keys]),
                    sorted(SOURCE_EVENT_PARSERS),
                    sorted(
                        parser for parser in SOURCE_EVENT_PARSERS if purpose(parser) == "result"
                    ),
                ),
            ),
        )
    )
    hosts: dict[str, dict[str, Any]] = {}
    result = {}
    for watch in indexes:
        if not identities[watch["watch_id"]] or identities[watch["watch_id"]] & continuations:
            continue
        host = urlsplit(watch["archive_url"] or watch["url"]).hostname
        if host is None:
            continue
        if host not in hosts:
            hosts[host] = admission(conn, config, host=host, now=now)
        value = hosts[host]
        if value["deferred"]:
            result[watch["watch_id"]] = value["reason"]
    return result


def report(
    conn: engine.Connection,
    config: Config,
    *,
    now: datetime,
    source: str | None = None,
    source_ref: str | None = None,
) -> dict[str, Any]:
    if not available(conn):
        return {"supported": False, "reason": "pressure_schema_unavailable"}
    if not _enabled(config):
        return {
            "supported": True,
            "disabled": True,
            "deferred": False,
            "snapshot_at": now.astimezone(UTC).isoformat(),
            "subjects": [],
            "hosts": [],
            "metadata_pending": False,
            "basis": "event pressure stage is off; expansion is not gated",
        }
    captured = policy(config)
    subjects = []
    for row in conn.execute(
        (
            "SELECT event_pressure_subjects.source, event_pressure_subjects.source_ref, event_pressure_subjects.enrolled_at, event_pressure_subjects.start_basis, event_pressure_subjects.revision, event_pressure_subjects.checked_sequence, event_pressure_subjects.scan_json, event_pressure_subjects.observation_json FROM event_pressure_subjects WHERE (CAST(%s AS TEXT) IS NULL OR source = %s) AND (CAST(%s AS TEXT) IS NULL OR source_ref = %s) ORDER BY source NULLS FIRST, source_ref NULLS FIRST"
        ),
        (source, source, source_ref, source_ref),
    ):
        subjects.append(
            {
                "source": row["source"],
                "source_ref": row["source_ref"],
                "enrolled_at": row["enrolled_at"],
                "start_basis": row["start_basis"],
                "status": subject_status(conn, row, now=now, policy_digest=captured["digest"]),
                "scan": json.loads(row["scan_json"]) if row["scan_json"] else None,
                "observation": json.loads(row["observation_json"])
                if row["observation_json"]
                else None,
            }
        )
    hosts = [
        admission(conn, config, host=row[0], now=now)
        for row in conn.execute(
            (
                "SELECT DISTINCT host FROM event_pressure_hosts WHERE (CAST(%s AS TEXT) IS NULL OR source = %s) AND (CAST(%s AS TEXT) IS NULL OR source_ref = %s) ORDER BY host NULLS FIRST"
            ),
            (source, source, source_ref, source_ref),
        )
    ]
    return {
        "supported": True,
        "snapshot_at": now.astimezone(UTC).isoformat(),
        "subjects": subjects,
        "hosts": hosts,
        "policy": captured,
        "metadata_pending": _pending(conn),
        "basis": "scheduling hints only; pagination and publication unassessed",
    }


def refresh(
    database: Database,
    archive: Any,
    config: Config,
    *,
    now: datetime,
    max_events: int | None = None,
    wall_seconds: float = 5.0,
) -> dict[str, Any]:
    if not _enabled(config):
        return {"disabled": True}
    from .event_pressure_refresh import refresh as run

    return run(database, archive, config, now=now, max_events=max_events, wall_seconds=wall_seconds)
