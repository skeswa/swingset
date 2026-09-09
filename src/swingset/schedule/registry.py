"""Registry cursor policy and archived dump diagnostics."""

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from swingset.fetch.archive import Archive
from swingset.model.ids import snapshot_id as make_snapshot_id
from swingset.model.ids import watch_id as make_watch_id
from swingset.schedule.watches import upsert_watch
from swingset.sources.wsdc_registry.adapter import SOURCE
from swingset.state.db import Database
from swingset.state.findings import Finding, replace_findings
from swingset.state.work import bump_revision


def seed_sweep(database: Database, start: int) -> None:
    if start < 1:
        raise ValueError("sweep start must be positive")
    with database.transaction() as conn:
        conn.execute(
            "INSERT INTO cursors(name,value) VALUES ('registry_sweep_next',?) ON CONFLICT(name) DO UPDATE SET value=excluded.value",
            (str(start),),
        )
        conn.execute(
            "INSERT INTO cursors(name,value) VALUES ('registry_sweep_misses','0') "
            "ON CONFLICT(name) DO UPDATE SET value='0'"
        )


def discover_registry(database: Database, now: datetime, *, batch_size: int = 350) -> int:
    conn = database.connection
    cursor = conn.execute("SELECT value FROM cursors WHERE name='registry_sweep_next'").fetchone()
    count = 0
    with database.transaction():
        if cursor:
            start = int(cursor[0])
            for wsdc_id in range(start, start + batch_size):
                spec = SOURCE.watch(wsdc_id)
                count += upsert_watch(conn, spec, now)
                conn.execute(
                    "UPDATE watches SET priority=5,notes='sweep' WHERE watch_id=?", (spec.watch_id,)
                )
        else:
            # A weekly probe is one sequential run that stops only after twenty
            # verified consecutive misses. A found dancer resets that count.
            probe_cursor = conn.execute(
                "SELECT value FROM cursors WHERE name='registry_probe_cursor'"
            ).fetchone()
            due = conn.execute(
                "SELECT value FROM cursors WHERE name='registry_probe_next_at'"
            ).fetchone()
            if probe_cursor is None and (due is None or datetime.fromisoformat(due[0]) <= now):
                top = int(
                    conn.execute("SELECT COALESCE(MAX(wsdc_id),0) FROM dancers").fetchone()[0]
                )
                conn.execute(
                    "INSERT INTO cursors(name,value) VALUES ('registry_probe_cursor',?)",
                    (str(top + 1),),
                )
                conn.execute(
                    "INSERT INTO cursors(name,value) VALUES ('registry_probe_misses','0') "
                    "ON CONFLICT(name) DO UPDATE SET value='0'"
                )
                probe_cursor = (str(top + 1),)
            if probe_cursor is not None:
                start = int(probe_cursor[0])
                for wsdc_id in range(start, start + min(batch_size, 20)):
                    spec = SOURCE.watch(wsdc_id)
                    count += upsert_watch(conn, spec, now)
                    conn.execute(
                        "UPDATE watches SET priority=5,notes='probe',next_check_at=? WHERE watch_id=?",
                        (now.isoformat(), spec.watch_id),
                    )
            # Daily trickle: at most 100 stale dancers, with a persistent day cursor.
            day = now.date().isoformat()
            trickle = conn.execute(
                "SELECT value FROM cursors WHERE name='registry_trickle_day'"
            ).fetchone()
            if trickle is None or trickle[0] != day:
                for row in conn.execute(
                    "SELECT wsdc_id FROM dancers WHERE registry_fetched_at<? ORDER BY registry_fetched_at LIMIT 100",
                    ((now - timedelta(days=365)).isoformat(),),
                ).fetchall():
                    spec = SOURCE.watch(int(row[0]))
                    count += upsert_watch(conn, spec, now)
                    conn.execute(
                        "UPDATE watches SET priority=5,notes='trickle',next_check_at=? WHERE watch_id=?",
                        (now.isoformat(), spec.watch_id),
                    )
                conn.execute(
                    "INSERT INTO cursors(name,value) VALUES ('registry_trickle_day',?) ON CONFLICT(name) DO UPDATE SET value=excluded.value",
                    (day,),
                )
    return count


def _lookup_outcome(conn: Any, wsdc_id: int) -> str | None:
    from swingset.model.observations import decode_payload

    found = conn.execute(
        "SELECT kind,payload_json FROM observations WHERE scope_kind='dancer' AND scope_id=? "
        "ORDER BY snapshot_id DESC LIMIT 1",
        (str(wsdc_id),),
    ).fetchone()
    if not found:
        return None
    payload = decode_payload(found[0], found[1])
    outcome = getattr(payload, "outcome", None)
    return str(outcome) if outcome is not None else None


def advance_sweep(database: Database, now: datetime | None = None) -> None:
    """Advance contiguous verified sweep/probe results; invalid gaps stop progress."""
    conn = database.connection
    sweep = conn.execute("SELECT value FROM cursors WHERE name='registry_sweep_next'").fetchone()
    if sweep is not None:
        current = int(sweep[0])
        misses_row = conn.execute(
            "SELECT value FROM cursors WHERE name='registry_sweep_misses'"
        ).fetchone()
        misses = int(misses_row[0]) if misses_row else 0
        while True:
            outcome = _lookup_outcome(conn, current)
            if outcome not in {"found", "not_found"}:
                break
            misses = misses + 1 if outcome == "not_found" else 0
            current += 1
            if misses >= 20:
                conn.execute("DELETE FROM cursors WHERE name='registry_sweep_next'")
                conn.execute("DELETE FROM cursors WHERE name='registry_sweep_misses'")
                return
        conn.execute("UPDATE cursors SET value=? WHERE name='registry_sweep_next'", (str(current),))
        conn.execute(
            "INSERT INTO cursors(name,value) VALUES ('registry_sweep_misses',?) "
            "ON CONFLICT(name) DO UPDATE SET value=excluded.value",
            (str(misses),),
        )
        return

    probe = conn.execute("SELECT value FROM cursors WHERE name='registry_probe_cursor'").fetchone()
    if probe is None:
        return
    current = int(probe[0])
    misses_row = conn.execute(
        "SELECT value FROM cursors WHERE name='registry_probe_misses'"
    ).fetchone()
    misses = int(misses_row[0]) if misses_row else 0
    while True:
        outcome = _lookup_outcome(conn, current)
        if outcome not in {"found", "not_found"}:
            break
        misses = misses + 1 if outcome == "not_found" else 0
        current += 1
        if misses >= 20:
            conn.execute(
                "DELETE FROM cursors WHERE name IN ('registry_probe_cursor','registry_probe_misses')"
            )
            due = (now or datetime.now().astimezone()).replace(microsecond=0) + timedelta(days=7)
            conn.execute(
                "INSERT INTO cursors(name,value) VALUES ('registry_probe_next_at',?) "
                "ON CONFLICT(name) DO UPDATE SET value=excluded.value",
                (due.isoformat(),),
            )
            return
    conn.execute("UPDATE cursors SET value=? WHERE name='registry_probe_cursor'", (str(current),))
    conn.execute(
        "INSERT INTO cursors(name,value) VALUES ('registry_probe_misses',?) "
        "ON CONFLICT(name) DO UPDATE SET value=excluded.value",
        (str(misses),),
    )


def crosscheck(database: Database, dump: Path, archive: Archive, now: datetime, run_id: str) -> int:
    body = dump.read_bytes()
    sha = archive.store_body(body)
    return _crosscheck_body(database, body, sha, now, run_id)


def replay_crosscheck(
    database: Database, body_sha256: str, archive: Archive, now: datetime, run_id: str
) -> int:
    """Re-run a cross-check directly from its archived manual body."""
    return _crosscheck_body(database, archive.read_body(body_sha256), body_sha256, now, run_id)


def _crosscheck_body(database: Database, body: bytes, sha: str, now: datetime, run_id: str) -> int:
    data: Any = json.loads(body)
    comparison_dump = (
        isinstance(data, dict)
        and isinstance(data.get("dancers"), list)
        and {"divisions", "event_occurrences", "events", "placements", "roles"} <= data.keys()
    )
    if isinstance(data, dict):
        data = data.get("dancers", data)
        if isinstance(data, dict):
            data = list(data.values())
    if not isinstance(data, list) or not all(isinstance(row, dict) for row in data):
        raise ValueError("registry dump must contain a dancer list or keyed mapping")
    conn = database.connection
    url = f"archive://registry-crosscheck/{sha}"
    watch_id = make_watch_id("crosscheck", "registry_dump", "MANUAL", url)
    snap_id = make_snapshot_id(now, sha)
    findings = []
    mirror = {int(row["wsdc_id"]): dict(row) for row in conn.execute("SELECT * FROM dancers")}
    seen = set()
    for row in data:
        number = row.get("wscid", row.get("wsdc_id"))
        if number is None and comparison_dump:
            number = row.get("id")
        if number is None:
            raise ValueError("dump row lacks wscid/wsdc_id; refusing guessed internal ids")
        number = int(number)
        seen.add(number)
        actual = mirror.get(number)
        discrepancy = actual is None or any(
            str(actual.get(key)) != str(row[key])
            for key in ("first_name", "last_name")
            if key in row
        )
        if discrepancy:
            findings.append(
                Finding(
                    "registry_diff",
                    "dancer",
                    str(number),
                    "warning",
                    "Registry dump differs from mirror",
                    {
                        "body_sha256": sha,
                        "snapshot_id": snap_id,
                        "dump_row": row,
                        "mirror_row": actual,
                    },
                    snapshot_id=snap_id,
                )
            )
    for number in sorted(mirror.keys() - seen):
        findings.append(
            Finding(
                "registry_diff",
                "dancer",
                str(number),
                "warning",
                "Mirror dancer absent from registry dump",
                {
                    "body_sha256": sha,
                    "snapshot_id": snap_id,
                    "dump_row": None,
                    "mirror_row": mirror[number],
                },
                snapshot_id=snap_id,
            )
        )
    with database.transaction():
        conn.execute(
            "INSERT OR IGNORE INTO watches(watch_id,source,kind,method,url,parser,source_ref,state,next_check_at,notes) "
            "VALUES (?,?,?,?,?,?,?,'archived',NULL,'manual registry dump')",
            (watch_id, "crosscheck", "registry_dump", "MANUAL", url, "registry_crosscheck", sha),
        )
        inserted = conn.execute(
            "INSERT OR IGNORE INTO snapshots(snapshot_id,watch_id,method,url,fetched_at,http_status,body_sha256,body_bytes,content_changed,run_id,via,classification,parse_status,parsed_at,parser_version) "
            "VALUES (?,?,?,?,?,200,?,?,1,?,'manual','OK','parsed',?,'1')",
            (
                snap_id,
                watch_id,
                "MANUAL",
                url,
                now.isoformat(),
                sha,
                len(body),
                run_id,
                now.isoformat(),
            ),
        )
        if inserted.rowcount:
            bump_revision(conn, "snapshots")
        replace_findings(
            conn,
            owner_kind="crosscheck",
            owner_id="registry",
            findings=tuple(findings),
            opened_at=now.isoformat(),
            run_id=run_id,
        )
        conn.execute(
            "INSERT INTO meta(key,value) VALUES ('registry_crosscheck_blob',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (sha,),
        )
    return len(findings)
