"""Reporting never turns an enumeration catalog into verified completion."""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from swingset import cli
from swingset.fetch.archive import Archive
from swingset.schedule.event_report import report
from swingset.state.db import open_database

NOW = datetime(2026, 9, 16, tzinfo=UTC)


def test_doctor_event_drilldown_keeps_unknowns_and_does_not_mutate(tmp_path, monkeypatch):
    (tmp_path / "operator-hold").touch()
    with open_database(tmp_path) as database:
        conn = database.connection
        conn.executemany(
            "INSERT INTO source_event_inventory "
            "(source,source_ref,enumeration_id,first_known_at,bootstrap_basis) "
            "VALUES (?,?,NULL,NULL,'legacy_watch')",
            [("scoringdance", "event:1"), ("scoringdance", "event:2")],
        )
        before = list(conn.iterdump())
        # A reporting connection must not wait for or mutate this writer.
        conn.execute("BEGIN IMMEDIATE")
        result = cli.doctor(
            argparse.Namespace(
                state=tmp_path,
                config=Path("config"),
                source="scoringdance",
                source_event="event:1",
            )
        )
        assert list(conn.iterdump()) == before
        conn.rollback()
    inventory = result["event_inventory"]
    assert inventory["supported"]
    assert inventory["operator_hold"]
    assert inventory["registered_events"] == 1
    assert inventory["legacy_unassessed_events"] == 1
    assert inventory["events"][0]["listed_pages"] is None
    assert inventory["local_stage_totals"] is None
    assert inventory["published_pages"] is None
    assert inventory["detail"]["acquired_pages"] is None
    assert not inventory["detail"]["known_pages_accounted_for"]
    blockers = inventory["detail"]["request_blockers"]
    assert blockers["operator_hold"]
    assert blockers["incomplete_gate_assessment"]
    assert "eligible" not in blockers
    emitted = []
    monkeypatch.setattr(cli, "log", lambda event, **fields: emitted.append((event, fields)))
    cli.daily_summary(result)
    assert emitted[-1][1]["event_inventory"] == inventory


def test_catalog_does_not_scan_artifacts_or_claim_local_stage_totals(tmp_path, monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("ordinary doctor catalog must not scan retained artifacts")

    monkeypatch.setattr(Archive, "read_body", forbidden)
    monkeypatch.setattr(Archive, "verify_body", forbidden)
    monkeypatch.setattr(Archive, "read_extract", forbidden)
    with open_database(tmp_path) as database:
        database.connection.execute(
            "INSERT INTO source_event_inventory VALUES ('example','event',NULL,NULL,'legacy_watch')"
        )
        result = cli.doctor(argparse.Namespace(state=tmp_path, config=Path("config")))
    inventory = result["event_inventory"]
    assert inventory["registered_events"] == 1
    assert inventory["local_stage_totals"] is None
    assert inventory["detail"] is None


def test_old_schema_reporting_is_explicitly_unavailable(tmp_path):
    import sqlite3

    with sqlite3.connect(":memory:") as conn:
        conn.execute("BEGIN")
        result = report(conn, Archive(tmp_path), now=NOW)
        assert not result["supported"]
        assert result["reason"] == "event_inventory_schema_unavailable"
        assert conn.execute("SELECT name FROM sqlite_master").fetchall() == []


def test_source_event_filter_requires_source_before_creating_state(tmp_path, capsys):
    assert cli.main(["doctor", "--state", str(tmp_path), "--source-event", "event:1"]) == 1
    assert "--source-event requires --source" in capsys.readouterr().err
    assert not (tmp_path / "state.sqlite").exists()


def test_json_drilldown_of_unknown_event_is_not_empty_success(tmp_path, capsys):
    with open_database(tmp_path):
        pass
    assert (
        cli.main(
            [
                "doctor",
                "--state",
                str(tmp_path),
                "--source",
                "example",
                "--source-event",
                "unknown",
                "--json",
            ]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)["event_inventory"]
    assert result["registered_events"] == 0
    assert result["detail"]["reason"] == "source_event_not_inventoried"
    assert result["local_stage_totals"] is None
