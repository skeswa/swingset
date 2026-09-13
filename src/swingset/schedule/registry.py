"""Registry cursor policy and archived dump diagnostics."""

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from swingset.fetch.archive import Archive
from swingset.model.ids import snapshot_id as make_snapshot_id
from swingset.model.ids import watch_id as make_watch_id
from swingset.schedule.confirmation import awaiting_first_number
from swingset.schedule.watches import upsert_watch
from swingset.sources.wsdc_registry.adapter import SOURCE, DancerPage
from swingset.state.db import Database
from swingset.state.findings import Finding, replace_findings
from swingset.state.verification import usable_verification
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
        conn.execute("DELETE FROM cursors WHERE name GLOB 'registry_probe_*'")
        conn.execute(
            "DELETE FROM meta WHERE key IN "
            "('registry_crosscheck_due','registry_crosscheck_completed')"
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
            count += _discover_probe(database, now, batch_size)
            # Daily trickle: at most 100 stale dancers, with a persistent day cursor.
            day = now.date().isoformat()
            trickle = conn.execute(
                "SELECT value FROM cursors WHERE name='registry_trickle_day'"
            ).fetchone()
            if trickle is None or trickle[0] != day:
                for row in conn.execute(
                    "SELECT d.wsdc_id,MAX(julianday(v.checked_at)) AS verified_at FROM dancers d "
                    "LEFT JOIN watches w ON w.source='wsdc_registry' AND w.source_ref='wsdc:'||d.wsdc_id "
                    "LEFT JOIN registry_verifications v ON v.watch_id=w.watch_id AND v.usable=1 "
                    "AND v.extract_version=? AND v.parser_version=? "
                    "GROUP BY d.wsdc_id HAVING verified_at IS NULL OR verified_at<julianday(?) "
                    "ORDER BY verified_at,d.wsdc_id LIMIT 100",
                    (
                        str(DancerPage.EXTRACT_VERSION),
                        str(DancerPage.PARSER_VERSION),
                        (now - timedelta(days=365)).isoformat(),
                    ),
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


def _cursor(conn: Any, name: str) -> str | None:
    row = conn.execute("SELECT value FROM cursors WHERE name=?", (name,)).fetchone()
    return str(row[0]) if row else None


def _set_cursor(conn: Any, name: str, value: str) -> None:
    conn.execute(
        "INSERT INTO cursors(name,value) VALUES (?,?) "
        "ON CONFLICT(name) DO UPDATE SET value=excluded.value",
        (name, value),
    )


def _discover_probe(database: Database, now: datetime, batch_size: int) -> int:
    conn = database.connection
    current = _cursor(conn, "registry_probe_cursor")
    started = _cursor(conn, "registry_probe_started_at")
    due = _cursor(conn, "registry_probe_next_at")
    completed = _cursor(conn, "registry_probe_last_completed_at")
    daily_due = awaiting_first_number(conn, now) and (
        completed is None or datetime.fromisoformat(completed) + timedelta(days=1) <= now
    )
    if current is None:
        if due is not None and datetime.fromisoformat(due) > now and not daily_due:
            return 0
        top = max(_local_found_bound(conn), _comparison_bound(database) or 0)
        current = str(top + 1)
        _set_cursor(conn, "registry_probe_cursor", current)
        started = None
    if started is None:
        # Older checkpoints lack a freshness boundary. Recheck their remaining tail.
        started = now.isoformat()
        _set_cursor(conn, "registry_probe_started_at", started)
        _set_cursor(conn, "registry_probe_misses", "0")
    count = 0
    for wsdc_id in range(int(current), int(current) + min(batch_size, 20)):
        spec = SOURCE.watch(wsdc_id)
        count += upsert_watch(conn, spec, now)
        schedule = conn.execute(
            "SELECT last_checked_at,next_check_at FROM watches WHERE watch_id=?", (spec.watch_id,)
        ).fetchone()
        checked = schedule[0]
        if checked is None or datetime.fromisoformat(checked) <= datetime.fromisoformat(started):
            conn.execute(
                "UPDATE watches SET next_check_at=? WHERE watch_id=?",
                (now.isoformat(), spec.watch_id),
            )
        elif _lookup_outcome(conn, wsdc_id, not_before=datetime.fromisoformat(started)) is None:
            retry_limit = now + timedelta(minutes=15)
            if schedule[1] is None or datetime.fromisoformat(schedule[1]) > retry_limit:
                conn.execute(
                    "UPDATE watches SET next_check_at=? WHERE watch_id=?",
                    (retry_limit.isoformat(), spec.watch_id),
                )
        conn.execute(
            "UPDATE watches SET priority=5,notes='probe' WHERE watch_id=?", (spec.watch_id,)
        )
    return count


def _lookup_outcome(conn: Any, wsdc_id: int, *, not_before: datetime | None = None) -> str | None:
    verification = usable_verification(conn, SOURCE.watch(wsdc_id).watch_id)
    if verification is None or (
        not_before is not None and datetime.fromisoformat(verification["checked_at"]) <= not_before
    ):
        return None
    return str(verification["outcome"])


def _comparison_bound(database: Database) -> int | None:
    conn = database.connection
    blob = conn.execute("SELECT value FROM meta WHERE key='registry_crosscheck_blob'").fetchone()
    if blob is None:
        return None
    sha = str(blob[0])
    cached = conn.execute("SELECT value FROM meta WHERE key='registry_sweep_bound_blob'").fetchone()
    bound = conn.execute("SELECT value FROM meta WHERE key='registry_sweep_dump_bound'").fetchone()
    if cached is not None and str(cached[0]) == sha and bound is not None:
        if not Archive(database.state_dir).blob_path(sha).exists():
            raise FileNotFoundError(f"archived registry comparison dump is missing: {sha}")
        return int(bound[0])
    rows, _ = _dump_rows(Archive(database.state_dir).read_body(sha), require_comparison_dump=True)
    result = max(int(row["id"]) for row in rows) if rows else 0
    conn.execute(
        "INSERT INTO meta(key,value) VALUES ('registry_sweep_bound_blob',?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (sha,),
    )
    conn.execute(
        "INSERT INTO meta(key,value) VALUES ('registry_sweep_dump_bound',?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (str(result),),
    )
    return result


def _local_found_bound(conn: Any) -> int:
    """Retain the highest locally verified found lookup."""
    from swingset.model.observations import decode_payload

    cached = conn.execute(
        "SELECT value FROM meta WHERE key='registry_sweep_local_bound'"
    ).fetchone()
    bound = int(cached[0]) if cached else 0
    for row in conn.execute(
        "SELECT kind,payload_json,scope_id FROM observations "
        "WHERE scope_kind='dancer' AND CAST(scope_id AS INTEGER)>? "
        "ORDER BY CAST(scope_id AS INTEGER) DESC",
        (bound,),
    ):
        payload = decode_payload(str(row[0]), str(row[1]))
        if getattr(payload, "outcome", None) == "found":
            bound = int(row[2])
            break
    dancer = conn.execute("SELECT MAX(wsdc_id) FROM dancers").fetchone()[0]
    bound = max(bound, int(dancer or 0))
    conn.execute(
        "INSERT INTO meta(key,value) VALUES ('registry_sweep_local_bound',?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (str(bound),),
    )
    return bound


def advance_sweep(database: Database, now: datetime | None = None) -> bool:
    """Advance contiguous verified sweep/probe results; invalid gaps stop progress."""
    conn = database.connection
    sweep = conn.execute("SELECT value FROM cursors WHERE name='registry_sweep_next'").fetchone()
    if sweep is not None:
        current = int(sweep[0])
        dump_bound = _comparison_bound(database)
        known_bound = max(dump_bound or 0, _local_found_bound(conn))
        misses_row = conn.execute(
            "SELECT value FROM cursors WHERE name='registry_sweep_misses'"
        ).fetchone()
        misses = int(misses_row[0]) if misses_row else 0
        misses = min(misses, max(0, current - known_bound - 1))
        while True:
            outcome = _lookup_outcome(conn, current)
            if outcome not in {"found", "not_found"}:
                break
            misses = misses + 1 if outcome == "not_found" and current > known_bound else 0
            if outcome == "found":
                known_bound = max(known_bound, current)
            current += 1
            if misses >= 20:
                if dump_bound is None:
                    raise RuntimeError(
                        "registry sweep completed without an archived comparison dump"
                    )
                blob = conn.execute(
                    "SELECT value FROM meta WHERE key='registry_crosscheck_blob'"
                ).fetchone()
                assert blob is not None
                conn.execute("DELETE FROM cursors WHERE name='registry_sweep_next'")
                conn.execute("DELETE FROM cursors WHERE name='registry_sweep_misses'")
                conn.execute(
                    "INSERT INTO meta(key,value) VALUES ('registry_crosscheck_due',?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (str(blob[0]),),
                )
                return True
        conn.execute("UPDATE cursors SET value=? WHERE name='registry_sweep_next'", (str(current),))
        conn.execute(
            "INSERT INTO cursors(name,value) VALUES ('registry_sweep_misses',?) "
            "ON CONFLICT(name) DO UPDATE SET value=excluded.value",
            (str(misses),),
        )
        return False

    probe = conn.execute("SELECT value FROM cursors WHERE name='registry_probe_cursor'").fetchone()
    if probe is None:
        return False
    started = _cursor(conn, "registry_probe_started_at")
    if started is None:
        return False
    current = int(probe[0])
    misses_row = conn.execute(
        "SELECT value FROM cursors WHERE name='registry_probe_misses'"
    ).fetchone()
    misses = int(misses_row[0]) if misses_row else 0
    while True:
        outcome = _lookup_outcome(conn, current, not_before=datetime.fromisoformat(started))
        if outcome not in {"found", "not_found"}:
            break
        misses = misses + 1 if outcome == "not_found" else 0
        current += 1
        if misses >= 20:
            conn.execute(
                "DELETE FROM cursors WHERE name IN "
                "('registry_probe_cursor','registry_probe_misses','registry_probe_started_at')"
            )
            completed = now or datetime.now().astimezone()
            _set_cursor(conn, "registry_probe_last_completed_at", completed.isoformat())
            due = completed + timedelta(days=7)
            conn.execute(
                "INSERT INTO cursors(name,value) VALUES ('registry_probe_next_at',?) "
                "ON CONFLICT(name) DO UPDATE SET value=excluded.value",
                (due.isoformat(),),
            )
            return False
    conn.execute("UPDATE cursors SET value=? WHERE name='registry_probe_cursor'", (str(current),))
    conn.execute(
        "INSERT INTO cursors(name,value) VALUES ('registry_probe_misses',?) "
        "ON CONFLICT(name) DO UPDATE SET value=excluded.value",
        (str(misses),),
    )
    return False


def crosscheck(database: Database, dump: Path, archive: Archive, now: datetime, run_id: str) -> int:
    body = dump.read_bytes()
    sha = archive.store_body(body)
    return _crosscheck_body(database, body, sha, now, run_id)


def archive_crosscheck_dump(
    database: Database, dump: Path, archive: Archive, now: datetime, run_id: str
) -> str:
    """Validate and durably archive the known comparison dump without comparing it."""
    body = dump.read_bytes()
    _dump_rows(body, require_comparison_dump=True)
    sha = archive.store_body(body)
    _record_crosscheck_snapshot(database, body, sha, now, run_id)
    return sha


def run_saved_crosscheck_if_due(
    database: Database, archive: Archive, now: datetime, run_id: str
) -> int | None:
    row = database.connection.execute(
        "SELECT value FROM meta WHERE key='registry_crosscheck_due'"
    ).fetchone()
    if row is None:
        return None
    sha = str(row[0])
    result = replay_crosscheck(database, sha, archive, now, run_id)
    with database.transaction() as conn:
        conn.execute("DELETE FROM meta WHERE key='registry_crosscheck_due'")
        conn.execute(
            "INSERT INTO meta(key,value) VALUES ('registry_crosscheck_completed',?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (sha,),
        )
    return result


def replay_crosscheck(
    database: Database, body_sha256: str, archive: Archive, now: datetime, run_id: str
) -> int:
    """Re-run a cross-check directly from its archived manual body."""
    return _crosscheck_body(database, archive.read_body(body_sha256), body_sha256, now, run_id)


def _crosscheck_body(database: Database, body: bytes, sha: str, now: datetime, run_id: str) -> int:
    data, comparison_dump = _dump_rows(body)
    _record_crosscheck_snapshot(database, body, sha, now, run_id)
    conn = database.connection
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
                        "snapshot_id": make_snapshot_id(now, sha),
                        "dump_row": row,
                        "mirror_row": actual,
                    },
                    snapshot_id=make_snapshot_id(now, sha),
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
                    "snapshot_id": make_snapshot_id(now, sha),
                    "dump_row": None,
                    "mirror_row": mirror[number],
                },
                snapshot_id=make_snapshot_id(now, sha),
            )
        )
    with database.transaction():
        replace_findings(
            conn,
            owner_kind="crosscheck",
            owner_id="registry",
            findings=tuple(findings),
            opened_at=now.isoformat(),
            run_id=run_id,
        )
    return len(findings)


def _dump_rows(
    body: bytes, *, require_comparison_dump: bool = False
) -> tuple[list[dict[str, Any]], bool]:
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
    if require_comparison_dump and not comparison_dump:
        raise ValueError("archive-only requires the known registry comparison dump shape")
    if require_comparison_dump:
        try:
            identifiers = [int(row["id"]) for row in data]
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("comparison dump has an invalid dancer id") from error
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("comparison dump has duplicate dancer ids")
    return data, comparison_dump


def _record_crosscheck_snapshot(
    database: Database, body: bytes, sha: str, now: datetime, run_id: str
) -> None:
    conn = database.connection
    rows, comparison_dump = _dump_rows(body)
    url = f"archive://registry-crosscheck/{sha}"
    watch_id = make_watch_id("crosscheck", "registry_dump", "MANUAL", url)
    snap_id = make_snapshot_id(now, sha)
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
        conn.execute(
            "INSERT INTO meta(key,value) VALUES ('registry_crosscheck_blob',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (sha,),
        )
        if comparison_dump:
            bound = max(int(row["id"]) for row in rows) if rows else 0
            conn.execute(
                "INSERT INTO meta(key,value) VALUES ('registry_sweep_bound_blob',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (sha,),
            )
            conn.execute(
                "INSERT INTO meta(key,value) VALUES ('registry_sweep_dump_bound',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(bound),),
            )
