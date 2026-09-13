"""Doctor and daily summaries expose observations without scheduling work."""

import argparse
from pathlib import Path

from swingset import cli
from swingset.state.db import open_database


def test_doctor_and_summary_report_service_without_mutation(tmp_path, monkeypatch):
    with open_database(tmp_path) as db:
        db.connection.execute(
            "INSERT INTO scheduler_offline_service VALUES ('parse','snapshot',3,"
            "'2026-09-13T00:00:00+00:00',1)"
        )
        before = list(db.connection.iterdump())
        result = cli.doctor(argparse.Namespace(state=tmp_path, config=Path("config")))
        assert list(db.connection.iterdump()) == before
    scheduler = result["scheduler"]
    assert scheduler["supported"]
    assert scheduler["observed_attributed_service"] == []
    assert scheduler["observed_offline_attempts"][0]["attempts"] == 3
    assert scheduler["initial_objectives"]["repair_requests_per_cycle"] == 2
    emitted = []
    monkeypatch.setattr(cli, "log", lambda event, **fields: emitted.append((event, fields)))
    cli.daily_summary(result)
    assert emitted[-1][0] == "summary"
    assert emitted[-1][1]["scheduler"] == scheduler
