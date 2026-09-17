"""Manual registry comparison artifacts are not ordinary parser snapshots."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from swingset.clock import FakeClock
from swingset.fetch.archive import Archive, canonical, digest
from swingset.model.ids import run_id as make_run_id
from swingset.schedule.derive import derive_one
from swingset.schedule.registry import replay_crosscheck
from swingset.schedule.watches import upsert_watch
from swingset.sources import get_page_kind
from swingset.sources.base import WatchSpec
from swingset.state import inputs
from swingset.state.db import Database, open_database
from swingset.state.work import WorkUnit


def _runtime_artifact(marker: bytes) -> dict[str, bytes]:
    runtime = {"runtime/swingset/__init__.py": marker}
    runtime["recipes/runtime.json"] = canonical(
        {
            "format": "swingset-runtime-recipe-v1",
            "files": {name: digest(body) for name, body in runtime.items()},
        }
    )
    return runtime


def _snapshot(database: Database, *, snapshot_id: str, source: str, parser: str, url: str) -> str:
    now = datetime(2026, 9, 17, tzinfo=UTC)
    spec = WatchSpec("", source, "round", "GET", url, parser)
    upsert_watch(database.connection, spec, now)
    database.connection.execute(
        "INSERT INTO snapshots(snapshot_id,watch_id,method,url,fetched_at,http_status,"
        "body_bytes,content_changed,run_id,classification,parse_status,parsed_at,parser_version) "
        "VALUES (?,?, 'GET', ?, ?, 200, 0, 1, ?, 'Ok', 'parsed', ?, '1')",
        (snapshot_id, spec.watch_id, url, now.isoformat(), make_run_id(now), now.isoformat()),
    )
    return spec.watch_id


def test_runtime_acceptance_skips_manual_crosscheck_but_keeps_parser_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = datetime(2026, 9, 17, tzinfo=UTC)
    clock = FakeClock(now)
    state = tmp_path / "state"
    archive = Archive(state)
    monkeypatch.setattr(inputs, "capture_runtime", lambda: _runtime_artifact(b"first\n"))

    with open_database(state) as database:
        first = inputs.capture(Path("config"), Path("overrides"), state, {})
        inputs.accept(database, first, clock)
        run_id = database.start_run(now)

        # Use the real producer and direct replay path for the synthetic
        # registry dump. This creates its MANUAL sentinel watch and snapshot.
        crosscheck_body = b"[]"
        crosscheck_sha = archive.store_body(crosscheck_body)
        assert replay_crosscheck(database, crosscheck_sha, archive, now, run_id) == 0
        crosscheck_snapshot = f"snap_20260917T000000Z_{crosscheck_sha[:12]}"
        crosscheck_watch = database.connection.execute(
            "SELECT watch_id FROM snapshots WHERE snapshot_id=?", (crosscheck_snapshot,)
        ).fetchone()[0]
        database.connection.execute(
            "UPDATE watches SET extract_version='manual-retained' WHERE watch_id=?",
            (crosscheck_watch,),
        )

        known_watch = _snapshot(
            database,
            snapshot_id="normal-parser-snapshot",
            source="wsdc_registry",
            parser="wsdc_registry.dancer",
            url="https://registry.example/dancer/1",
        )
        unknown_watch = _snapshot(
            database,
            snapshot_id="unknown-parser-snapshot",
            source="wsdc_registry",
            parser="wsdc_registry.future_page",
            url="https://registry.example/future/1",
        )
        database.connection.execute(
            "UPDATE watches SET extract_version='old' WHERE watch_id IN (?,?)",
            (known_watch, unknown_watch),
        )

        # An unsupported genuine page parser remains ordinary parse work and
        # still fails page-kind lookup; only the exact manual artifact is out.
        with pytest.raises(KeyError, match="unknown page kind: wsdc_registry.future_page"):
            get_page_kind("wsdc_registry.future_page")

        monkeypatch.setattr(inputs, "capture_runtime", lambda: _runtime_artifact(b"second\n"))
        second = inputs.capture(Path("config"), Path("overrides"), state, {})
        changed = inputs.accept(database, second, clock)
        assert "recipe/runtime" in changed

        pending = {
            str(row[0])
            for row in database.connection.execute(
                "SELECT unit_id FROM pending_work WHERE stage='parse' AND unit_kind='snapshot'"
            )
        }
        assert pending == {"normal-parser-snapshot", "unknown-parser-snapshot"}
        assert (
            database.connection.execute(
                "SELECT extract_version FROM watches WHERE watch_id=?", (known_watch,)
            ).fetchone()[0]
            is None
        )
        assert (
            database.connection.execute(
                "SELECT extract_version FROM watches WHERE watch_id=?", (unknown_watch,)
            ).fetchone()[0]
            is None
        )
        assert (
            database.connection.execute(
                "SELECT extract_version FROM watches WHERE watch_id=?", (crosscheck_watch,)
            ).fetchone()[0]
            == "manual-retained"
        )

        unknown_unit = WorkUnit("parse", "snapshot", "unknown-parser-snapshot")
        unknown_failure = derive_one(database, archive, unknown_unit, second, clock, run_id)
        assert unknown_failure.failed
        assert unknown_failure.reason == "unit_exception"
        blocked_attempt = database.connection.execute(
            "SELECT attempt_id,outcome,reason_code,evidence_json,requirement_id "
            "FROM work_attempts WHERE stage='parse' AND unit_id=? "
            "ORDER BY attempt_id DESC LIMIT 1",
            (unknown_unit.unit_id,),
        ).fetchone()
        assert blocked_attempt is not None
        assert tuple(blocked_attempt)[1:3] == ("blocked", "unit_exception")
        assert json.loads(blocked_attempt[3]) == {
            "exception": "KeyError",
            "message": "'unknown page kind: wsdc_registry.future_page'",
        }
        failure_attempt_record = tuple(blocked_attempt)
        requirement_id = str(blocked_attempt[4])
        failure_finding = database.connection.execute(
            "SELECT * FROM findings WHERE finding_id=?", (requirement_id,)
        ).fetchone()
        assert failure_finding is not None
        failure_finding_record = tuple(failure_finding)
        requirement_attempts = tuple(
            tuple(row)
            for row in database.connection.execute(
                "SELECT * FROM requirement_attempts WHERE requirement_id=? ORDER BY rowid",
                (requirement_id,),
            )
        )

        # Direct replay remains supported and does not manufacture parse work.
        assert replay_crosscheck(database, crosscheck_sha, archive, now, run_id) == 0
        assert (
            database.connection.execute(
                "SELECT value FROM meta WHERE key='registry_crosscheck_blob'"
            ).fetchone()[0]
            == crosscheck_sha
        )
        assert (
            database.connection.execute(
                "SELECT 1 FROM pending_work WHERE stage='parse' AND unit_id=?",
                (crosscheck_snapshot,),
            ).fetchone()
            is None
        )

        # Do not silently rewrite or delete an already-recorded stale queue
        # item; it is separate failure evidence for coordinator remediation.
        stale_enqueued_at = "2026-09-16T00:00:00+00:00"
        database.connection.execute(
            "INSERT INTO pending_work(stage,unit_kind,unit_id,enqueued_at) "
            "VALUES ('parse','snapshot',?,?)",
            (crosscheck_snapshot, stale_enqueued_at),
        )
        monkeypatch.setattr(inputs, "capture_runtime", lambda: _runtime_artifact(b"third\n"))
        third = inputs.capture(Path("config"), Path("overrides"), state, {})
        assert "recipe/runtime" in inputs.accept(database, third, clock)
        assert (
            database.connection.execute(
                "SELECT enqueued_at FROM pending_work WHERE stage='parse' AND unit_id=?",
                (crosscheck_snapshot,),
            ).fetchone()[0]
            == stale_enqueued_at
        )
        assert (
            tuple(
                database.connection.execute(
                    "SELECT attempt_id,outcome,reason_code,evidence_json,requirement_id "
                    "FROM work_attempts WHERE stage='parse' AND unit_id=? "
                    "ORDER BY attempt_id DESC LIMIT 1",
                    (unknown_unit.unit_id,),
                ).fetchone()
            )
            == failure_attempt_record
        )
        assert (
            tuple(
                database.connection.execute(
                    "SELECT * FROM findings WHERE finding_id=?", (requirement_id,)
                ).fetchone()
            )
            == failure_finding_record
        )
        assert (
            tuple(
                tuple(row)
                for row in database.connection.execute(
                    "SELECT * FROM requirement_attempts WHERE requirement_id=? ORDER BY rowid",
                    (requirement_id,),
                )
            )
            == requirement_attempts
        )
        assert not inputs.accept(database, third, clock)
